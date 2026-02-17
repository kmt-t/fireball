# Specification Gap Backlog

This document tracks missing details in the natural language specifications that are required for a complete WIT definition and TLA+ verification.
エージェント向けの構造化インデックスは `.agent/brain/backlog.atc` を参照。

## 1. Loader (module_loader)
- [x] **Dependency Management**: `resolve-imports` API追加。未解決インポート時はロード済み/実行不可。@see vsoc.wit, loader.md §4.1
- [x] **Module Lifecycle**: `unload` API追加。bump_allocator LIFO制約を明記。@see vsoc.wit, loader.md §4.1
- [x] **Memory Constraints**: FB_CONF_MAX_MODULES/FUNCTIONS/EXPORTS/GLOBALS/IMPORTS定義。@see loader.md §4.2
- [x] **Verification Levels**: V1-V5まで規定（magic, version, bounds, order, type）。@see loader.md §4.3

## 2. Logger (logging)
- [x] **WIT Interface**: logger engine, dictionary, system-loggerに@pre/@post/@inv契約追加。@see services.wit
- [x] **Dictionary Management**: ROM配置、エントリフォーマット確定。@see logging.md §4.2
- [x] **Buffer Policy**: FINALIZED: Overwrite。@see logging.md §4.1, services.wit engine @inv
- [x] **Flush Interface**: COOS Idle Hook連携プロトコル確定。@see logging.md §4.3, services.wit engine.flush

## 3. vSoC and vMMIO
- [x] **VDMA Detail**: 純粋MMIOトラップ + fireball_call(VDMA_START)ラッパー。@see vmmio.md §4.1
- [x] **Virtual Interrupts**: 静的1:1マッピング、別config(irq_mapping_config)。@see vmmio.md §4.6, vsoc.wit
- [x] **Trap Handling**: WASMの呼び出し規約に委譲。明示的保存/復元不要。@see fireball_syscall_interface.md §10

## 4. Memory Management
- [x] **Ownership Metadata**: memory-infoにowner(task-id)フィールド追加。allocate時に自動設定。@see memory.wit, memory_manager.md §6
- [x] **Shared Memory Lifecycle**: allocator-freesポリシー。IPC経由で完了通知後allocatorが解放。@see memory.wit, memory_manager.md §7

## 5. IPC and Channels
- [x] **Handoff Conditions**: TLA+ ipc_handoff.tla 6ケースからWIT契約を導出。@see services.wit ipc-router.send/recv
- [x] **Message Format Stability**: kv-pair bitfield FINALIZED (scope:3+dtype:5, key:24, value:32 = 64bit)。@see types.wit
