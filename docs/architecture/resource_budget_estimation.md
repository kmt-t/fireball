# 物理リソース予算 & C++ 実装規模見積もり仕様書
<!-- traceability: {Resource_Estimation_Model} -->

## 1. 目的

<!-- traceability: {Resource_Estimation_Model} {Size_20KSLOC} {GLOBAL_StrictMemoryLimit} {ConsolidatedHeap} {ROMParsing} {META_ZeroCostAbstraction} -->
本ドキュメントは、Python リファレンスシミュレータ（`experiments/pysim`）の検証結果と各コンポーネントのアルゴリズムに基づく。Clang 17+ の組み込み C++ へ本実装した際の**実装規模（LOC）**と**物理リソース予算（ROM / RAM）**を見積もる仕様書である。

見積もりは静的配置とゼロ動的アロケーションを前提とする。AoS `flat_map_view` と `[[clang::musttail]]` の採用も前提に含める。

`backlog_list.md` の物理リソース予算の厳密な再見積もりタスクにおける正本ドキュメントとして、ROM（`.rodata` / `.text`）に配置可能な不変データと、RAM（SRAM / `.data` / `.bss`）に配置すべき可変状態・バッファ・スタックを厳密に区別して算出する。

---

## 2. C++ 実装規模見積もり（テストコード除く）

最新の作業ツリーにある pysim 製品コード46ファイルを再計測した結果、物理行数は18,701行である。この値には空行とコメントを含み、QA、ベンチマーク、シナリオ、`main.py`、`aobench.py` は含めない。

旧見積もりと同じ移行係数1.15〜1.25を参考適用すると、C++ 実装規模は約21.5〜23.4 KSLOCとなる。この係数は暫定値であり、Pythonの物理行数と要求上のSLOCは計測定義が異なるため、この結果だけでは20 KSLOC予算への適合を判定できない。

### Tier 別の pysim 実測と C++ 規模の参考推定

| 対象 | pysim 製品コード物理行数 | C++23 規模の参考推定 |
| :--- | ---: | ---: |
| Tier 1 Core | 1,786 | 約2.1〜2.2 KSLOC |
| Tier 1 Interface | 759 | 約0.9〜1.0 KSLOC |
| Tier 2 Runtime | 12,644 | 約14.5〜15.8 KSLOC |
| Tier 3 Executer | 2,336 | 約2.7〜2.9 KSLOC |
| Tier 3 Platform | 345 | 約0.4 KSLOC |
| 共通入口（`system.py`、`__init__.py`） | 831 | 約1.0 KSLOC |
| **合計** | **18,701** | **約21.5〜23.4 KSLOC** |

要求上のコード規模上限は20 KSLOCである。現時点の参考推定は数値上この上限を上回るため、達成済みとは判定しない。C++実装時には同じ対象範囲・SLOC定義で計測し、実測値に基づいて構成要素別の見積もりを更新する。

---

## 3. ARMv8-M物理リソース予算（TBD）

要求では最小構成をCortex-M33 / RAM 32KB / ROM 96KB、想定構成をCortex-M33 / RAM 64KB / ROM 128KBとし、評価対象を最小構成としている。この容量条件は [`requirement_list.md`](docs/requires/requirement_list.md) の規範的な制約である。

この資料では、ARMv8-M向けの実使用量内訳、対象ボード、OS stack、JIT cache の物理配置、メモリ保護方式、実機性能はTBDとする。旧案の物理配置内訳とROMコードサイズ推定は要求値ではないため削除した。要求容量への適合性は、対象と実装を確定してから実測で判定する。

`experiments/pysim` の設定値とx64測定値は、シミュレータの容量制限やホスト上の挙動を表す。ARMv8-Mの物理資源量を示すものではない。対象CPU、ボード、リンカ配置、OS、必要な割込みstack、JIT保護方式を確定した後に見積もる。

## 4. 実装規模とARMv8-M予算の判定
<!-- traceability: {Size_20KSLOC} -->

1. **実装規模の成立性**:
   - コード規模要求 `Size_20KSLOC` の上限は20 KSLOCである。pysim物理行数からの参考推定は約21.5〜23.4 KSLOCとなるため、SLOC定義でのC++実測まで予算達成を確定しない。
2. **RAM / ROM リソースの成立性**:
   - 要求の上限は最小構成でRAM 32KB / ROM 96KB、想定構成でRAM 64KB / ROM 128KBである。
   - ARMv8-M実装の物理配置・使用量はTBDであり、`system_config.md` のpysim設定値は容量適合の証拠に使わない。
