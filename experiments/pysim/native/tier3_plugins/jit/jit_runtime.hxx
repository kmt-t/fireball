#pragma once
/** @file
 * @brief JITキャッシュの固定状態と手続きの宣言。実装はjit_runtime.cxxに置く。
 */
#include <array>
#include <limits>
#include <type_traits>

#include "../../../tier2_runtime/abi/interpreter_abi.hxx"
#include "common_code_abi.hxx"
#include "jit_chain_diagnostics.hxx"
#include "jit_internal.hxx"
#include "jit_runtime_abi.hxx"
#include "profiling.hxx"

#ifndef FB_CONF_JIT_CACHE_BANK_ENTRY_CAPACITY
#error "FB_CONF_JIT_CACHE_BANK_ENTRY_CAPACITY is required"
#endif
#ifndef FB_CONF_JIT_CACHE_BANK_CAPACITY_BYTES
#error "FB_CONF_JIT_CACHE_BANK_CAPACITY_BYTES is required"
#endif

namespace fireball {
constexpr std::uint32_t jit_trace_header_bytes = FB_CONF_JIT_X64_TRACE_HEADER_BYTES;
constexpr auto jit_invalid_pc = std::numeric_limits<wasm_pc_t>::max();
constexpr block_index_t jit_invalid_block_index = FIREBALL_INVALID_BLOCK_INDEX;
constexpr std::uint32_t jit_cache_bank_count = 3;
constexpr std::uint32_t jit_cache_bank_entry_capacity = FB_CONF_JIT_CACHE_BANK_ENTRY_CAPACITY;
constexpr std::uint32_t jit_cache_total_entry_capacity =
    jit_cache_bank_count * jit_cache_bank_entry_capacity;
constexpr std::size_t jit_compile_scratch_bytes =
    jit_trace_header_bytes + kMaxBodyBytes + kTraceBoundaryMetadataBytes;
constexpr code_offset_t jit_invalid_code_offset = std::numeric_limits<code_offset_t>::max();
static_assert(jit_cache_bank_entry_capacity > 0);
static_assert(kMaxTraceInstructions <= std::numeric_limits<jit_body_count_t>::max());
// 0xFF is the unset BasicBlock reference, so it cannot identify a cache entry.
static_assert(jit_cache_total_entry_capacity <= std::numeric_limits<std::uint8_t>::max());
static_assert(jit_compile_scratch_bytes <= FB_CONF_JIT_CACHE_BANK_CAPACITY_BYTES);

/** @brief 一つの常駐トレースを識別する12バイトのキャッシュ要素。 */
struct jit_cache_entry {
  wasm_pc_t head_pc = jit_invalid_pc;
  code_offset_t code_offset = jit_invalid_code_offset;
  std::uint16_t cache_token = 0;
  /** @brief コード領域を持つ常駐要素かを返す。 */
  bool resident() const { return code_offset != jit_invalid_code_offset; }
};
static_assert(sizeof(jit_cache_entry) == 12);
/** @brief キャッシュ要素と物理バンクの非所有参照。 */
struct jit_trace_location {
  jit_cache_entry *trace_entry = nullptr;
  std::uint32_t bank_id = jit_cache_bank_count;
};
/** @brief 固定容量バンクのPC索引と単調増加する書き込み位置。 */
struct jit_cache_bank {
  std::array<jit_cache_entry, jit_cache_bank_entry_capacity> entries{};
  std::uint32_t entry_count = 0;
  std::uint32_t resident_bytes = 0;
  std::uint32_t write_offset_bytes = 0;
  std::uint32_t capacity_bytes = 0;
  std::uint32_t entry_capacity = 0;
  std::uint32_t region_offset_bytes = 0;
  /** @brief 指定PC以上の先頭要素を求める。 */
  std::uint32_t lower_bound(wasm_pc_t pc) const;
  /** @brief 完全一致する常駐要素を返し、不在ならnullptrを返す。 */
  jit_cache_entry *find_entry_by_pc(wasm_pc_t pc);
  /** @brief 固定容量内にトレースを割り当てる。容量不足はfalseを返す。 */
  bool allocate(std::uint8_t *region, wasm_pc_t pc, code_offset_t size, std::uint16_t token,
                code_offset_t &code_offset);
};

/** @brief 計測を持たない製品構成の空の方策。 */
struct jit_no_measurements {};
template <class Measurement> struct jit_runtime_traits {
  using profile_type = typename Measurement::profile_type;
  using measurement_type = Measurement;
};
template <> struct jit_runtime_traits<void> {
  using profile_type = jit_profile;
  using measurement_type = jit_no_measurements;
};
template <class Measurement>
concept jit_measurement_policy =
    std::is_void_v<Measurement> || requires { typename Measurement::profile_type; };

/** @brief 可変の常駐コード、PC索引、chain逆引きと3世代の役割を所有する。 */
struct jit_cache_state {
  static constexpr std::uint32_t inbound_bucket_count = 64;
  static constexpr std::uint8_t no_reference = 0xFFu;
  std::array<jit_cache_bank, jit_cache_bank_count> banks{};
  std::array<std::uint8_t, jit_cache_total_entry_capacity> pc_index{};
  std::array<std::uint8_t, inbound_bucket_count> inbound_chain_heads{};
  std::array<std::uint8_t, jit_cache_total_entry_capacity> inbound_chain_next{};
  std::uint32_t pc_index_count = 0;
  std::uint32_t active_bank_id = 0, warm_bank_id = 1, oldest_bank_id = 2;
  executable_memory code_memory{};
};

/** @brief 借用カード領域と、所有する履歴・候補キューを保持する。 */
template <class Profile> struct jit_hotspot_state {
  std::uint8_t *card_states = nullptr;
  std::uint8_t *dirty_cards = nullptr;
  card_index_t card_count = 0;
  std::uint8_t card_shift = 0;
  std::uint32_t dirty_mask_bytes = 0;
  std::uint32_t *aging_cursor = nullptr;
  std::uint32_t aging_step_units = 1, aging_scan_bytes = 1;
  std::uint32_t aging_cursor_storage = 0;
  std::array<jit_history_record, FB_CONF_JIT_HISTORY_CAPACITY> history_records{};
  std::array<wasm_pc_t, FB_CONF_JIT_COMPILE_QUEUE_CAPACITY> compile_queue_items{};
  jit_history observation_history{};
  jit_compile_queue compile_queue{};
  Profile configuration{};
};

/** @brief モジュール、キャッシュ、候補処理を結び付けるJIT実行状態。 */
template <jit_measurement_policy Measurement> struct jit_runtime final {
  using profile_type = typename jit_runtime_traits<Measurement>::profile_type;
  using measurement_type = typename jit_runtime_traits<Measurement>::measurement_type;
  [[no_unique_address]] measurement_type measurements{};
  jit_cache_state cache{};
  jit_hotspot_state<profile_type> hotspots{};
  fb_native_jit_provider provider{};
  const fireball_wasm_module_execution_view_native *module_view = nullptr;
  /** @brief 保存領域内に固定状態を初期化する。 */
  jit_runtime() = default;
  /** @brief コード領域と自己参照を持つ実行状態の複製を禁止する。 */
  jit_runtime(const jit_runtime &) = delete;
  jit_runtime &operator=(const jit_runtime &) = delete;
  ~jit_runtime();
  const jit_wasm_block *make_compiler_block_view(block_index_t index, jit_wasm_block &output) const;
  /** @brief QA計測用に製品または参照ランタイムのブロック情報を参照する。 */
  const jit_wasm_block *diagnostic_block_view(block_index_t index, jit_wasm_block &output) const
    requires(!std::is_void_v<Measurement>);
  const jit_wasm_block *block_metadata_for_trace(const jit_trace_header &trace,
                                                 jit_wasm_block &output) const;
  /** @brief 候補マスクで指定PCが履歴記録対象かを調べる。 */
  bool is_profiled_pc(wasm_pc_t pc);
  /** @brief コンパイル対象外のPCを候補マスクから外す。 */
  void exclude_compile_candidate(wasm_pc_t pc);

private:
  // Groups code-cache writes into one patch transaction. On x64, permission is
  // granted once; other targets can still switch protection at this boundary.
  // Nested writers use the enclosing transaction.
  struct patch_transaction {
    jit_runtime *runtime;
    bool began;
    /** @brief 外側のトランザクションがなければコード書き込みを開始する。 */
    explicit patch_transaction(jit_runtime *owner);
    /** @brief 自分が開始した書き込みだけを確定する。 */
    void close();
    ~patch_transaction();
    /** @brief 二重の確定を生じるトランザクションの複製を禁止する。 */
    patch_transaction(const patch_transaction &) = delete;
    patch_transaction &operator=(const patch_transaction &) = delete;
  };
  /** @brief 一候補を一度だけコンパイルし、実サイズに従ってActiveへ配置する。 */
  int compile_one_candidate(block_index_t block_index, wasm_pc_t pc);

public:
  /** @brief 処理予算内の候補を消費し、コンパイル成功件数を返す。 */
  int compile_queued_candidates(std::uint32_t budget);
  /** @brief 検証済みのブロック識別子とPCを固定履歴リングへ記録する。 */
  void record_block_visit(block_index_t block_index, wasm_pc_t pc);
  /** @brief 検証済みのブロック識別子とPCを固定履歴リングへ記録する。 */
  void record_block_visit(wasm_pc_t pc)
    requires(!std::is_void_v<Measurement>);
  /** @brief 履歴を一度ずつ消費し、カード状態とコンパイルキューを更新する。 */
  void analyze_observation_history(bool yielded);
  /** @brief 索引変更前にBasicBlockのキャッシュ参照を無効化する。 */
  void invalidate_block_cache_references();
  jit_cache_entry &cache_entry_at_reference(std::uint8_t reference);
  /** @brief 固定参照から物理バンク番号を求める。 */
  std::uint32_t bank_of_reference(std::uint8_t reference) const;
  /** @brief 常駐要素からPC索引、逆引き連結表、BasicBlock参照を再構成する。 */
  void rebuild_cache_indices();
  /** @brief 後続PCの逆引きバケットを求める。 */
  static std::uint32_t chain_bucket_for_pc(wasm_pc_t pc);
  /** @brief 一意なPC索引を検索する。cache missは空の参照を返す。 */
  jit_trace_location find_cached_trace_by_pc(wasm_pc_t pc);
  /** @brief 常駐位置を72要素の固定参照へ変換する。 */
  std::uint8_t cache_reference(jit_trace_location location) const;
  /** @brief 有効なBasicBlock参照を常駐位置に変換する。 */
  jit_trace_location trace_location_from_reference(std::uint8_t reference_value);
  jit_trace_header *trace_header(jit_cache_entry value) const;
  /** @brief 常駐トレースの実行エントリアドレスを返す。 */
  std::uintptr_t trace_entry_address(jit_cache_entry value) const;
  /** @brief Loaderが解決した後続PCを返す。 */
  wasm_pc_t trace_successor_pc(jit_cache_entry value) const;
  block_index_t trace_successor_block_index(jit_cache_entry value) const;
  /** @brief Loaderが解決したループ先PCを返す。 */
  wasm_pc_t trace_loop_target_pc(jit_cache_entry value) const;
  /** @brief 静的な前方チェイニングが可能なソースかを判定する。 */
  bool can_chain_from(jit_cache_entry value) const;
  /** @brief 後続PCとフレーム境界が連結可能かを判定する。 */
  bool can_chain_to(jit_cache_entry value, jit_cache_entry target) const;
  /** @brief QA計測で実行済みトレース終端を保留する。 */
  void record_runtime_trace_exit(block_index_t source_block_index, jit_stack_words_t result_words,
                                 frame_depth_t frame_depth)
    requires(!std::is_void_v<Measurement>);
  /** @brief QA計測で実際に解釈器へ戻った後続辺を分類する。 */
  void record_runtime_transition(block_index_t target_block_index, wasm_pc_t target_pc)
    requires(!std::is_void_v<Measurement>);
  /** @brief QA計測で常駐先へのチェイン構築判定を記録する。 */
  void record_chain_construction(jit_cache_entry source)
    requires(!std::is_void_v<Measurement>);
  /** @brief 既存の連結先を参照する。未連結ならnullptrを返す。 */
  const jit_trace_header *chain_target_header(const jit_trace_header &source) const;
  /** @brief 連結先が変わる場合だけコードヘッダを書き換える。 */
  void set_chain_target(jit_cache_entry source, const jit_cache_entry *target);
  /** @brief Active/Warmの後続へ連結する。Oldestへは既存の連結だけを維持する。 */
  void link_resident_successor(jit_cache_entry source);
  /** @brief カードの2ビット状態を読み出す。 */
  std::uint32_t card_state(std::uint32_t card) const;
  /** @brief カードの2ビット状態を更新する。 */
  void set_card_state(std::uint32_t card, std::uint32_t state);
  /** @brief 常駐トレースのカードをコンパイル済みにする。 */
  void mark_trace_card_compiled(wasm_pc_t pc);
  /** @brief 計測ポリシーに従って予算付きのカード減衰を実行する。 */
  int age_hotspot_cards();
  /** @brief dirty領域を予算内で走査し、履歴候補だけを減衰する。 */
  int decay_dirty_cards();
  /** @brief 一バンクを破棄し、カードと参照索引を更新する。 */
  void retire_cache_bank(std::uint32_t id);
  /** @brief 破棄対象への生存バンクからの連結を逆引き表で解除する。 */
  void unlink_retiring_bank(std::uint32_t id);
  /** @brief Oldestを破棄し、3世代の役割を一段進める。 */
  void rotate_cache_banks();
  /** @brief コンパイル済みコードをコピーし、配置依存の参照を解決する。 */
  void copy_trace_into_cache(jit_cache_entry value, const std::uint8_t *blob, std::uint32_t bytes);
  /** @brief QA用コードをActiveへ配置する。容量不足なら一度ローテーションする。 */
  bool insert_trace_fixture(const std::uint8_t *blob, std::uint32_t bytes,
                            std::uint16_t cache_token)
    requires(!std::is_void_v<Measurement>);
  /** @brief 全世代の常駐トレースを移動せずに検索する。 */
  jit_trace_location lookup_cached_trace(block_index_t block_index, wasm_pc_t pc);
  /** @brief 全世代の常駐トレースを移動せずに検索する。 */
  jit_trace_location lookup_cached_trace(wasm_pc_t pc)
    requires(!std::is_void_v<Measurement>);
  /** @brief 後続から順に連結全体のスタック上限と実行数を更新する。 */
  void refresh_chain_requirements();
};
} // namespace fireball

namespace fireball {
/** @brief Loaderが確定したブロックのコンパイル候補スコアを求める。 */
std::int64_t score_block(const jit_wasm_block &block);

/** @brief ホスト契約を確定するJITプラグインのABI手続き。 */
template <jit_measurement_policy Measurement> struct jit_provider_adapter {
  using runtime_type = jit_runtime<Measurement>;
  /** @brief すべての常駐コードと連結を破棄する。 */
  static int fb_jit_runtime_flush(runtime_type *runtime);
  /** @brief カード領域のサイズと減衰設定を入口で検証して結び付ける。 */
  static int fb_jit_runtime_bind_cards(runtime_type *runtime, std::uint8_t *states,
                                       [[maybe_unused]] std::uint32_t state_bytes,
                                       std::uint8_t *dirty, std::uint32_t dirty_bytes,
                                       std::uint32_t cards, std::uint32_t shift,
                                       std::uint32_t *cursor, std::uint32_t units,
                                       std::uint32_t scan_bytes);
  /** @brief 候補設定を検証し、ブロック単位で候補マスクを構築する。 */
  static int fb_jit_runtime_bind_profile(runtime_type *runtime,
                                         typename runtime_type::profile_type p);
  /** @brief コンパイルキューを指定予算で処理する。 */
  static int fb_jit_runtime_compile(runtime_type *runtime, std::uint32_t budget);
  /** @brief 実行境界の履歴を処理し、必要なら候補をコンパイルする。 */
  static int finish_boundary(runtime_type *runtime, int yielded, std::uint32_t budget);
  /** @brief 配置されたランタイムの寿命を終了する。 */
  static void fb_jit_runtime_close(runtime_type *runtime);
  /** @brief Interpreterへ常駐トレースの実行情報を渡す。 */
  static int lookup_runtime_trace(std::uintptr_t owner, block_index_t block_index, wasm_pc_t pc,
                                  fb_native_jit_trace *output);
  /** @brief 対象ブロックの実行履歴を記録する。 */
  static int observe_runtime_block(std::uintptr_t owner, block_index_t block_index, wasm_pc_t pc);
  /** @brief Interpreterの終了状態をJITの境界処理へ変換する。 */
  static int finish_runtime_boundary(std::uintptr_t owner, std::uint32_t status,
                                     std::uint32_t budget);
  /** @brief カード状態と2つのビット領域の必要サイズを返す。 */
  static std::size_t workspace_size(const fireball_wasm_module_execution_view_native *view);
  /** @brief 状態と作業領域を実行領域のアラインメントへ切り上げる。 */
  static std::size_t data_region_size(const fireball_wasm_module_execution_view_native *view);
  /** @brief ホストの実行領域アラインメントを返す。 */
  static std::size_t fb_jit_plugin_alignment();
  /** @brief ランタイム状態、作業領域、コード領域の総サイズを返す。 */
  static std::size_t
  fb_jit_plugin_required_bytes(const fireball_wasm_module_execution_view_native *view);
  /** @brief 借用領域にランタイムを構築し、コード領域と候補設定を初期化する。 */
  static runtime_type *fb_jit_plugin_init(std::uint8_t *region, [[maybe_unused]] std::size_t bytes,
                                          const fireball_wasm_module_execution_view_native *view,
                                          std::uint32_t module_id);
  static const fb_native_jit_provider *fb_jit_plugin_provider(runtime_type *runtime);
};
} // namespace fireball
