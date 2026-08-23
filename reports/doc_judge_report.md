# Fireball 仕様矛盾・セマンティック整合性監査レポート (LLM as a Judge)

- **監査サブグラフ総数**: 132
- **合格 (PASS)**: 105
- **警告 (WARN)**: 23
- **不合格 (FAIL)**: 4

---

## 1. 検出された矛盾・警告 (Issues Found)

### 🟡 WARN: `{META_FlatMapIndexed}`
- **サマリー**: The design generally adheres to the {META_FlatMapIndexed} requirement, but there is a critical contradiction regarding the prohibition of dynamic memory allocation in the IPC Router section.
- **詳細項目**:
  - **[ERROR]** `sec:components/tier1_interface/ipc_router.md#3.1 データ構造`: The design specifies the use of 'fireball::static_flat_map' to 'eliminate dynamic memory allocation', but the definition of {META_FlatMapIndexed} allows for 'static_flat_map' or 'sorted arrays'. While consistent with the meta-keyword, the IPC Router design explicitly claims to eliminate dynamic allocation, yet other sections (like vMMIO) use 'std::flat_map' which typically requires a dynamic allocator unless a custom allocator is provided. This creates a systemic inconsistency in how 'FlatMap' is implemented across different tiers (Static vs Dynamic).
  - **[WARNING]** `sec:components/tier2_runtime/runtime_vmmio.md#3.1 データ構造`: The design uses 'std::flat_map<uint32_t, uint32_t>' for PTE management. According to {META_NoStdVector} (which is a sibling meta-requirement in the same definition section), dynamic std::vector is prohibited. Since std::flat_map is typically backed by a std::vector, using the standard library version without specifying a static allocator contradicts the overall system constraint of avoiding dynamic memory in the runtime.

### 🔴 FAIL: `{GLOBAL_ComponentHarness}`
- **サマリー**: Critical contradiction between the architectural policy for harness application and the actual implementation in the component design sections.
- **詳細項目**:
  - **[ERROR]** `sec:architecture/concept_harness.md#2.1 適用範囲と分類`: The policy explicitly states that Tier 1 components do NOT require a harness ('Tier 1: ❌ 不要') because they use URI-based dynamic DI. However, sec:components/tier1_core/os_coos.md (a Tier 1 component) implements a 'coos_harness' and explicitly references {GLOBAL_ComponentHarness}, directly contradicting the defined scope.
  - **[ERROR]** `sec:components/tier3_jit/jit_compiler.md#2. アーキテクチャ分類`: The policy explicitly states that Tier 3 components do NOT require a harness ('Tier 3: ❌ 不要') as they are single-responsibility leaf components. However, this section tags itself with {GLOBAL_ComponentHarness}, implying the application of a pattern that is defined as 'excessive' for this tier.
  - **[WARNING]** `sec:components/tier1_core/os_coos.md#ハーネスによる依存性注入パターン`: The design uses a Python-like pseudo-code example to illustrate the harness, whereas the overarching design goal in sec:architecture/concept_harness.md specifies a 'C++20/23 Concepts-based' zero-cost implementation. This creates a conceptual gap between the high-level design and the illustrative example.

### 🟡 WARN: `{GLOBAL_PeriodicTask}`
- **サマリー**: The design generally aligns with the definition of {GLOBAL_PeriodicTask}, but there is a critical gap in the OS Scheduler's implementation details regarding how periodic tasks are actually triggered and managed.
- **詳細項目**:
  - **[WARNING]** `sec:components/tier1_core/os_scheduler.md#4.1 アルゴリズム`: The scheduler mentions that the idle handler executes 'Periodic Task etc.', but the actual mechanism for triggering periodic tasks (e.g., a timer queue, tick counter, or delta list) is missing from the algorithm description. While the JIT section references 'register_periodic_callback', the OS Scheduler does not define the API or the internal logic to manage these callbacks.
  - **[WARNING]** `sec:components/tier1_core/os_scheduler.md#4.2 状態遷移図`: The state transition diagram and the accompanying table do not show a transition or trigger for periodic task activation. It only covers spawn, yield, CSP, and interrupts. There is no 'Timer/Tick' event that would move a periodic task from a waiting state to the READY state.

### 🟡 WARN: `{MemoryBoundaryCheck}`
- **サマリー**: The requirement {MemoryBoundaryCheck} is generally well-integrated across the interpreter and runtime, but there is a critical semantic contradiction in the JIT compiler section regarding the definition of 'Boundary Check'.
- **詳細項目**:
  - **[ERROR]** `sec:components/tier3_jit/jit_compiler.md#7.2 安全性制約と方策`: Contradiction in requirement application: The definition of {MemoryBoundaryCheck} explicitly refers to 'memory access boundary checks' to ensure isolation (guest linear memory). However, in the JIT compiler section, {MemoryBoundaryCheck} is used to describe 'cache overflow checks' (checking if the JIT code buffer is full and discarding the Old region). These are two entirely different types of boundary checks (Memory Safety vs. Buffer Management).
  - **[WARNING]** `sec:components/tier2_runtime/runtime_interpreter.md#コールフレーム（call_frame）`: Incomplete traceability: The section is tagged with {MemoryBoundaryCheck} in the traceability comment, but the table content does not specify how the 'Stack Boundary' (スタック境界) is validated or if it is governed by the {MemoryBoundaryCheck} logic.

### 🟡 WARN: `{GLOBAL_InterruptWakeup}`
- **サマリー**: The design generally implements the requirement, but there is a conceptual contradiction regarding the notification model between the OS core and the HAL layer.
- **詳細項目**:
  - **[WARNING]** `sec:components/tier3_platform/platform_hal.md#4.1 アルゴリズム`: Contradiction in notification mechanism: The HAL section references both {TaskPollInterruptFlag} (polling model) and {GLOBAL_InterruptWakeup} (event-driven wakeup model) for the same interrupt notification process. The OS design (os_coos.md and os_scheduler.md) explicitly defines an event-driven model where ISRs post INT events to a queue for the scheduler to process, which contradicts the 'polling' nature of {TaskPollInterruptFlag}.
  - **[WARNING]** `sec:components/tier1_core/os_scheduler.md#4.2 状態遷移図`: State transition inconsistency: The state diagram shows a transition from RUNNING to InterruptWait triggered by '[interrupt occurs]'. However, the accompanying 'Note' and the 'Interrupt Wakeup' algorithm in os_coos.md state that ISRs do not directly change task states but post events to a queue. A task should typically transition to InterruptWait via a system call (like wait_event), not be forced into it by an external interrupt while running.

### 🟡 WARN: `{PhysicalPassthrough}`
- **サマリー**: The design sections reference {PhysicalPassthrough} for various HAL functions, but there is a conceptual gap between the 'Direct Physical Access' definition and the 'IPC-based' implementation described in the HAL sections.
- **詳細項目**:
  - **[WARNING]** `sec:components/tier3_platform/platform_hal.md`: The definition of {PhysicalPassthrough} specifies 'direct access to physical resources without memory copying'. However, the referencing HAL sections (e.g., 'Non-standard Control' and 'URI/IPC Interface') describe these operations as being performed via IPC messages and URI-based interfaces. There is a contradiction between 'direct physical access' (bypassing layers) and 'IPC-mediated access' (which involves the IPC router and service facades).
  - **[WARNING]** `sec:components/tier3_platform/platform_hal.md#非標準制御 (control)`: The 'Non-standard Control' section is tagged with {PhysicalPassthrough} but describes a command-based IPC mechanism (`control(id, cmd, params)`). This is a functional abstraction, not a physical passthrough of memory or registers, making the traceability tag conceptually incorrect.
  - **[WARNING]** `sec:components/tier2_runtime/runtime_vmmio.md`: While the vMMIO section mentions 'physical passthrough pages' in the FlatMap PTE management, it does not explicitly define the mechanism by which a page is marked as 'passthrough' versus 'emulated' (vMMIO_TrapAndEmulate), leaving the implementation of the 'direct access' requirement ambiguous.

### 🟡 WARN: `{CSP_Handoff}`
- **サマリー**: The design generally implements the CSP_Handoff requirement, but there is a critical contradiction regarding the execution path (Scheduler-mediated vs. Scheduler-bypass).
- **詳細項目**:
  - **[ERROR]** `sec:components/tier1_core/os_coos.md#4.1 アルゴリズム`: Contradiction in execution flow: The Definition {CSP_Handoff} explicitly states that the target task is transitioned to Ready and switched 'via the scheduler' (スケジューラを介して). However, the Design section 4.1 states that the handoff occurs 'without going through the scheduler' (スケジューラを介さず即座に) and 'completely bypasses the OS scheduler's queue processing overhead' (OSスケジューラのキュー処理オーバーヘッドを完全にバイパスし). This is a direct contradiction in the architectural mechanism.
  - **[WARNING]** `sec:components/tier1_core/os_coos.md#6.1 検証対象の不変条件`: The design introduces a new constraint 'FB_CONF_MAX_CONSECUTIVE_HANDOFFS' to prevent starvation/infinite loops during handoff chains. This constraint is not mentioned in the original Definition {CSP_Handoff}, creating a gap between the high-level requirement and the implementation invariant.

### 🟡 WARN: `{RSPMinimalSet}`
- **サマリー**: The design sections correctly reference the requirement and establish the architectural flow, but the 'minimal set' of GDB RSP commands is not actually defined.
- **詳細項目**:
  - **[WARNING]** `sec:components/tier2_runtime/debug/debug_gdb_rsp.md#1. 概要`: The section states it 'defines the minimum set of GDB RSP commands required by VSCode', but the provided text contains no actual list of commands (e.g., g, m, M, s, c, etc.). The requirement {RSPMinimalSet} is referenced, but the specific 'set' remains unspecified in the design.

### 🔴 FAIL: `{Debug_Integrated}`
- **サマリー**: The design sections fail to implement the core functional requirements of {Debug_Integrated}, specifically the integration of a profiler and dynamic testing tools.
- **詳細項目**:
  - **[ERROR]** `sec:components/tier2_runtime/debug/debug_manager.md and sec:components/tier2_runtime/runtime_interpreter.md`: The definition of {Debug_Integrated} explicitly requires the system to 'incorporate profiler and dynamic testing tool functions'. However, the referencing design sections only describe debugger-related mechanisms (GDB RSP, breakpoints, and cache flushing). There is no mention of profiler implementation or dynamic testing tool functionality.
  - **[WARNING]** `sec:components/tier2_runtime/runtime_interpreter.md#4.1 アルゴリズム`: The 'Debug Hook' mentioned in the algorithm section only addresses breakpoint detection and control delegation to the Debugger, which is a subset of general debugging but does not satisfy the 'Profiler/Dynamic Test Tool' requirement of {Debug_Integrated}.

### 🟡 WARN: `{vMMIO_TrapAndEmulate}`
- **サマリー**: The requirement {vMMIO_TrapAndEmulate} is consistently implemented across the design sections. The definition of trapping guest memory access to call host hooks is directly realized through the 'register-hook' mechanism and the vMMIO architectural layer.
- **詳細項目**:
  - **[WARNING]** `sec:components/tier2_runtime/runtime_vsoc.md#`register-hook``: Minor signature mismatch: The 'Arguments' list includes 'harness: vsoc_harness', but this parameter is missing from the formal 'Signature' line provided in the same table and the corresponding table in runtime_vmmio.md.

### 🔴 FAIL: `{ContextPointerRegister}`
- **サマリー**: The design fails to implement the core requirement of the {ContextPointerRegister} definition.
- **詳細項目**:
  - **[ERROR]** `sec:components/tier2_runtime/runtime_interpreter.md#実行コンテキスト（execution_context）`: The definition of {ContextPointerRegister} explicitly requires the context pointer to be held in a 'physical register' (物理レジスタに保持する) to ensure high performance. However, the design section only defines the 'execution_context' as a logical structure (virtual CPU registers) without specifying which physical register is dedicated to holding the pointer to this structure. There is no mention of the hardware register mapping required to satisfy the 'High' priority requirement.
  - **[WARNING]** `sec:plans/backlog_list.md#Phase 1.2`: The backlog item for 'execution_context' implementation references {ContextPointerRegister} but only lists the internal members of the context (PC, SP, etc.), failing to include the task of mapping the context pointer to a physical register as mandated by the requirement.

### 🟡 WARN: `{TaskPollInterruptFlag}`
- **サマリー**: The design implements the notification mechanism, but there is a conceptual ambiguity regarding 'polling' versus 'wakeup'.
- **詳細項目**:
  - **[WARNING]** `sec:components/tier3_platform/platform_hal.md#4.1 アルゴリズム`: The definition of {TaskPollInterruptFlag} explicitly specifies a 'polling' model ('タスクがポーリングにより割り込みフラグをチェックする'). However, the design section 4.1 describes a 'wakeup' model ('COOSスケジューラに対して関連タスクのウェイクアップを要求する'). While these can coexist (wakeup to trigger the task, then polling the flag to identify the cause), the design does not explicitly mention the task's polling action, creating a slight inconsistency in the described notification flow.

### 🟡 WARN: `{CleanArchitecture}`
- **サマリー**: The design references the Clean Architecture principle, but the provided BDD (Block Definition Diagram) shows dependency directions that potentially contradict the 'dependency towards the interior' rule.
- **詳細項目**:
  - **[WARNING]** `sec:architecture/architecture_overview.md#2.2 コンポーネント定義図 (BDD)`: The definition of {CleanArchitecture} requires restricting dependency directions 'inward' (typically towards business logic/entities). However, the BDD shows dependencies flowing from the Guest Layer down to the Hardware Layer (App -> vSoC -> COOS/IPCR -> HAL -> HW). While this is a standard layered architecture, it is not explicitly mapped to Clean Architecture circles (Entities, Use Cases, Interface Adapters, Frameworks). There is a risk that the 'Hardware Layer' or 'Kernel Layer' is being treated as the core, whereas Clean Architecture would dictate that the core be independent of the HAL/Hardware.
  - **[WARNING]** `sec:architecture/architecture_overview.md#1. アーキテクチャコンセプト`: The design mentions 'URI-based abstraction' and 'IPC router' as the means to achieve {CleanArchitecture}. While this implements decoupling, the specification does not define which layer constitutes the 'Internal/Core' to verify if dependencies are indeed restricted to the interior.

### 🟡 WARN: `{META_Risk_Tiering}`
- **サマリー**: The keyword {META_Risk_Tiering} is used as a traceability tag in multiple design and planning sections, but there is no concrete evidence of its application (e.g., defined risk tiers or adjusted verification levels) in the referencing sections.
- **詳細項目**:
  - **[WARNING]** `sec:components/tier1_interface/interface_wit.md, sec:plans/backlog_list.md, sec:plans/roadmap_phase.md`: The definition of {META_Risk_Tiering} requires 'adjusting verification levels according to importance and uncertainty.' While the keyword is tagged in the architecture principles and the Phase 0.8 quality gate, the documents do not specify which components are assigned to which risk tier or how the verification levels differ across those tiers. The tag is used for traceability, but the actual design implementation of the 'tiering' logic is missing.

### 🟡 WARN: `{Fast_Path_GPIO}`
- **サマリー**: The design implements the Fast_Path_GPIO requirement through two different mechanisms (Direct Syscall and vMMIO), which creates a conceptual ambiguity regarding the 'single' fast path implementation.
- **詳細項目**:
  - **[WARNING]** `sec:components/tier1_core/system_syscall.md vs sec:components/tier2_runtime/runtime_vmmio.md`: There is a conceptual overlap/contradiction in how {Fast_Path_GPIO} is realized. Section 6.2 implements it as a direct system call (`fireball_call` / `FB_SYSCALL_TRIGGER_SET_PIN`), whereas the vMMIO section lists {Fast_Path_GPIO} as a traceability target for a memory-mapped I/O approach (PhysicalPassthrough). It is unclear if the 'Fast Path' is intended to be a function call (Trap) or a memory access (MMIO).
  - **[WARNING]** `sec:components/tier3_platform/platform_hal.md`: The HAL concept section claims 'All accesses pass through the IPC router' ({IPCRouter}), but {Fast_Path_GPIO} is explicitly defined in the requirements as a mechanism to 'bypass the abstraction layer'. The design does not specify how the HAL handles the bypass path if the IPC router is the mandatory gateway.

### 🟡 WARN: `{Challenge_CoosBlockedList}`
- **サマリー**: The design sections consistently address the challenge defined in the requirement list. The trade-off between management cost and real-time performance is explicitly resolved via an event-driven queue structure.
- **詳細項目**:
  - **[WARNING]** `sec:components/tier1_core/os_scheduler.md#タスク生成 (spawn)`: The 'spawn' function is tagged with {Challenge_CoosBlockedList} in its traceability metadata, but the function's logic (creating a task and adding it to the READY queue) does not directly interact with the BLOCKED list management. While not a contradiction, the traceability link is weak compared to the ADR section.

### 🟡 WARN: `{Challenge_DebuggerResource}`
- **サマリー**: The design sections reference the challenge, but the specific constraints regarding JIT coexistence and memory limits are not explicitly addressed in the implementation details.
- **詳細項目**:
  - **[WARNING]** `sec:components/tier1_core/system_config_details.md#2.6 デバッガ`: The definition of {Challenge_DebuggerResource} specifically highlights the 'constraints of combining debug buffers with JIT in a tiny memory environment'. While a packet buffer size is defined (1024), there is no design specification explaining how this buffer interacts with or is partitioned from the JIT cache to prevent memory exhaustion.
  - **[WARNING]** `sec:components/tier1_core/system_config_details.md#2.7 型定義・予約値`: The traceability tag {Challenge_DebuggerResource} is present in the section header, but the content (task_id definitions) has no logical connection to the debugger resource or memory constraints challenge.

### 🟡 WARN: `{WIT_Interface_Purpose}`
- **サマリー**: The referencing design sections provide functional purposes but fail to describe the 'logical invariants' explicitly required by the definition.
- **詳細項目**:
  - **[WARNING]** `sec:components/tier1_core/system_syscall.md#6.1. 役割 and sec:components/tier1_interface/interface_wit.md#1. 目的`: The definition of {WIT_Interface_Purpose} explicitly requires the description of 'logical invariants' (論理的な不変条件). While the design sections describe the 'purpose' and 'role' (intercepting calls, adhering to WASI 0.2), they do not define any specific logical invariants that must remain constant across the interface implementation.

### 🟡 WARN: `{DebuggerLabelTableSwitch}`
- **サマリー**: The design references the requirement and explains the 'off' state behavior, but fails to specify the actual mechanism or content of the 'debug-use' handler table.
- **詳細項目**:
  - **[WARNING]** `sec:components/tier2_runtime/debug/debug_manager.md`: The definition of {DebuggerLabelTableSwitch} requires switching the handler table to a 'debug-use' version. While the design section 6.1 explains that the table is NOT switched when debugging is disabled to avoid overhead, it does not describe what the debug-specific handler table contains or how the switch is triggered when debugging is enabled.

### 🟡 WARN: `{HAL_Interface}`
- **サマリー**: The design sections implement the functional aspect of the HAL interface, but there is a discrepancy regarding the transport mechanism specified in the definition.
- **詳細項目**:
  - **[WARNING]** `sec:components/tier3_platform/platform_hal.md`: The definition of {HAL_Interface} explicitly states that the interface is provided 'via IPC' (IPC経由で提供). However, the design for 'データの書き込み (write)' references a 'shm-id' (shared memory handle) for the source buffer. While shared memory is often used in conjunction with IPC, the design does not explicitly specify how the IPC mechanism triggers the operation or manages the synchronization of this shared memory, leaving a gap in the traceability of the 'via IPC' requirement.
  - **[WARNING]** `sec:requires/requirement_list.md#3.1.3`: The keyword {HAL_Interface} is defined twice identically in the requirement table. This is a redundancy error in the definition section.

### 🟡 WARN: `{ServiceSelfReboot}`
- **サマリー**: The design section acknowledges the requirement for service self-reboot, but the implementation mechanism is underspecified compared to the definition.
- **詳細項目**:
  - **[WARNING]** `sec:architecture/architecture_overview.md#1. アーキテクチャコンセプト`: The definition section specifies two distinct requirements: {ServiceSelfReboot} (autonomous reboot/recovery) and {SelfReboot_via_Event} (event-triggered reboot). While the architecture overview references {ServiceSelfReboot}, it completely omits the reference to {SelfReboot_via_Event}, leaving the trigger mechanism for the self-reboot process undefined in the high-level design.

### 🟡 WARN: `{IPC_Resource_Isolation}`
- **サマリー**: The design references the requirement for IPC resource isolation, but the implementation details are insufficient to verify 'complete separation and protection' as demanded by the definition.
- **詳細項目**:
  - **[WARNING]** `sec:architecture/architecture_overview.md#ヒープパーティション`: The definition of {IPC_Resource_Isolation} requires 'complete separation and protection' (完全分離と保護). However, the design section only mentions 'registry management' and 'generation cookies' for handling inconsistencies during reboot. It does not specify the mechanism for active protection or how the resources are physically/logically isolated during normal operation to prevent unauthorized access or interference between services.

### 🟡 WARN: `{COOS_Deterministic}`
- **サマリー**: The design section acknowledges the requirement for deterministic execution but fails to specify the mechanism for limiting context switches to explicit points.
- **詳細項目**:
  - **[WARNING]** `sec:components/tier1_core/os_scheduler.md#1. コンセプト`: The definition of {COOS_Deterministic} explicitly requires that context switches be 'limited to explicit points' to ensure deterministic execution. While the design section mentions 'deterministic execution' and 'cooperative multitasking', it does not explicitly define or commit to the constraint of limiting switches to specific, explicit points (e.g., yield points or CSP synchronization points), leaving the implementation detail ambiguous.

### 🔴 FAIL: `{LowOverheadSwitch}`
- **サマリー**: The referencing design section fails to implement or describe the technical mechanisms required to achieve the LowOverheadSwitch requirement.
- **詳細項目**:
  - **[ERROR]** `sec:components/tier1_core/os_scheduler.md#タスク生成 (spawn)`: The section is tagged with {LowOverheadSwitch}, but the content only describes task creation (spawn) and memory allocation for TCBs. It contains no design details regarding the minimization of register saving/restoring or the mechanism to achieve 'transition in a few cycles' as mandated by the definition.
  - **[WARNING]** `sec:components/tier1_core/os_scheduler.md#タスク生成 (spawn)`: There is a traceability mismatch. {LowOverheadSwitch} is a performance requirement related to the context switching process, whereas the referenced section describes the initialization/allocation phase of a task, which does not directly contribute to the overhead of the switch itself.

### 🟡 WARN: `{WIT_Common_Types}`
- **サマリー**: The referencing section acknowledges the requirement via traceability tags, but fails to provide the actual implementation or definition of the common types.
- **詳細項目**:
  - **[WARNING]** `sec:components/tier1_interface/interface_wit.md#1. 目的`: The section references {WIT_Common_Types} in the traceability tags, but the content only describes the general purpose of the document. There is no actual definition of the 'basic type definitions shared across multiple WIT definitions' as required by the definition section.

### 🟡 WARN: `{ServiceFacade}`
- **サマリー**: The design section references the requirement but fails to specify the 'type-safe method' implementation mandated by the definition.
- **詳細項目**:
  - **[WARNING]** `sec:components/tier1_interface/ipc_router.md#5.3 サービスファサード`: The definition of {ServiceFacade} explicitly requires it to be a 'thin wrapper providing type-safe methods' (型安全なメソッドとして提供する薄いラッパー). However, the design section only mentions hiding IPC primitives and achieving IoC, omitting any mention of how type safety is ensured or implemented.

### 🟡 WARN: `{InterpreterContextStackless}`
- **サマリー**: The design section references the requirement but fails to specify the technical implementation details of the 'stackless' mechanism.
- **詳細項目**:
  - **[WARNING]** `sec:components/tier2_runtime/runtime_interpreter.md#1. コンセプト`: The design section mentions {InterpreterContextStackless} as a concept but does not describe how the 'stackless' execution (avoiding C-stack usage) is achieved. While the definition specifies 'not using C-stack', the design only lists it as a traceability tag without providing the architectural approach to ensure this constraint is met.

### 🟡 WARN: `{DynamicMmap}`
- **サマリー**: The design section references {DynamicMmap} as a conceptual driver for the vMMIO architecture, but it fails to specify the actual mechanism for 'temporary mapping' or 'shared memory ID' handling required by the definition.
- **詳細項目**:
  - **[WARNING]** `sec:components/tier2_runtime/runtime_vmmio.md#1. コンセプト`: The definition of {DynamicMmap} explicitly requires the ability to 'specify a shared memory ID' and 'temporarily map external buffers to vMMIO space'. While the design section mentions that 'Dynamic SHM pages' can be registered in the FlatMap, it does not describe the API, the lifecycle of the 'temporary' mapping, or how the 'shared memory ID' is resolved to a PTE. The implementation detail is missing, leaving the requirement only partially addressed at a conceptual level.

### 🟡 WARN: `{HistoryBuffer}`
- **サマリー**: The referencing design acknowledges the use of HistoryBuffer but fails to specify how the 'ring buffer' characteristic defined in the requirements is implemented or utilized.
- **詳細項目**:
  - **[WARNING]** `sec:components/tier3_jit/jit_runtime_hotspot.md#1. コンセプト`: The definition of {HistoryBuffer} explicitly specifies a 'ring-shaped buffer' (リング状のバッファ). However, the referencing design section only mentions the general role of monitoring frequency and identifying hotspots without confirming the adoption of the ring buffer structure or its capacity/management logic.

---

## 2. 全評価結果一覧

| キーワード / 要求ID | 判定 | 評価サマリー | 検出Issue数 |
| :--- | :---: | :--- | :---: |
| `{META_ConfigurableSystem}` | 🟢 PASS | The referencing design sections are highly consistent with the definition of {META_ConfigurableSystem}. The requirement to use header macros and constexpr for compile-time static determination of system parameters is rigorously applied across the HAL, JIT, vMMIO, and Logging components. | 0 |
| `{META_3TierSeparation}` | 🟢 PASS | The referencing design sections consistently implement the 3-tier decomposition and encapsulation rules defined by {META_3TierSeparation}. | 0 |
| `{META_Static_Resolution}` | 🟢 PASS | The keyword {META_Static_Resolution} is consistently applied across all referencing design sections. The definition 'resolving matters at compile-time to minimize overhead' is strictly followed in the system configuration, vMMIO static regions, and the constexpr JIT assembler. | 0 |
| `{META_RecoveryStrategy}` | 🟢 PASS | The design sections consistently implement the {META_RecoveryStrategy} definition. The transition from error codes to action-oriented recovery strategies (ignore, retry, restart, panic) is uniformly applied across the WIT interfaces, system services, and runtime components. | 0 |
| `{META_FlatMapIndexed}` | 🟡 WARN | The design generally adheres to the {META_FlatMapIndexed} requirement, but there is a critical contradiction regarding the prohibition of dynamic memory allocation in the IPC Router section. | 2 |
| `{LowLatencyJIT}` | 🟢 PASS | The requirement {LowLatencyJIT} is consistently implemented across all referencing design sections. The design transforms the high-level goal of 'minimizing compile latency' into concrete technical strategies: Copy-and-Patch JIT, transaction-based MPU switching, and hotspot-driven selective compilation. | 0 |
| `{IPCRouter}` | 🟢 PASS | The design sections are highly consistent with the definition of {IPCRouter}. All referencing components (Logging, System Service, HAL) explicitly adhere to the requirement that all system calls and communications must pass through the IPC Router for routing and access control. | 0 |
| `{META_FaultIsolation}` | 🟢 PASS | The referencing design sections are highly consistent with the definition of {META_FaultIsolation}. The requirement to prevent fault propagation via memory partitioning is concretely implemented across multiple layers: hardware MPU regions, independent heap partitions, and ownership-based shared memory transfer. | 0 |
| `{META_AccessDictionary}` | 🟢 PASS | The keyword {META_AccessDictionary} is consistently applied across the IPC Router and WASM Runtime Loader designs. It is correctly used to denote the indexing of data for optimized runtime access, specifically through the use of sorted arrays and binary search (O(log N)) to avoid dynamic allocation. | 0 |
| `{GLOBAL_IdleDetection}` | 🟢 PASS | The keyword {GLOBAL_IdleDetection} is consistently defined and implemented across the OS core, logging system, and JIT compiler. The design sections accurately reflect the requirement to detect idle states for background processing (log flushing and batch compilation). | 0 |
| `{JIT_CopyAndPatch}` | 🟢 PASS | The requirement {JIT_CopyAndPatch} is consistently implemented and referenced across all design sections. The definition of using instruction templates and patching is accurately reflected in the architectural concepts, the detailed compilation procedure, and the implementation backlog. | 0 |
| `{JIT_MultiBuffer_Cache}` | 🟢 PASS | The specification for {JIT_MultiBuffer_Cache} is highly consistent across all referencing sections. The definition of a 3-bank circular cache (2KB x 3) is strictly maintained in the configuration, runtime memory policies, and the detailed JIT compiler algorithm. | 0 |
| `{OwnershipTransfer}` | 🟢 PASS | The requirement {OwnershipTransfer} is consistently and comprehensively implemented across the IPC Router, vMMIO runtime, and Platform Memory sections. The logical flow from high-level definition to state machine and low-level hardware enforcement (TLB/PTE) is well-aligned. | 0 |
| `{SimpleJITArchitecture}` | 🟢 PASS | The design sections consistently implement the requirements of {SimpleJITArchitecture}, specifically focusing on efficient operation within a small JIT cache area through selective compilation and optimized indexing. | 0 |
| `{RoleBasedAccessControl}` | 🟢 PASS | The design sections are highly consistent with the definition of {RoleBasedAccessControl}. The requirement for static access control based on URIs and a role matrix is fully implemented through a C++23 constexpr matrix, a defined lookup pipeline, and a DAG-based deadlock prevention strategy. | 0 |
| `{ThreadedInterpreter}` | 🟢 PASS | The design sections consistently implement the requirements for {ThreadedInterpreter}. The definition's core pillars—table dispatch for speed and environment pointers for hierarchical access—are explicitly detailed in the runtime interpreter's concept and algorithm sections. | 0 |
| `{BufferedLogging}` | 🟢 PASS | The design sections fully implement and are consistent with the {BufferedLogging} requirement. The mechanism of using a ring buffer for temporary storage and deferred output during idle periods is explicitly detailed across the architecture, configuration, and algorithmic sections. | 0 |
| `{GLOBAL_ComponentHarness}` | 🔴 FAIL | Critical contradiction between the architectural policy for harness application and the actual implementation in the component design sections. | 3 |
| `{META_StaticDI}` | 🟢 PASS | The referencing design sections are consistent with the definition of {META_StaticDI}. The design consistently applies the concept of 'Harness' structures (coos_harness, vsoc_harness) to achieve static dependency injection and compile-time binding as required. | 0 |
| `{DictionaryBasedIPC}` | 🟢 PASS | The design sections consistently implement the {DictionaryBasedIPC} requirement. The mechanism of converting string keys to static dictionary offsets to reduce IPC transfer volume is clearly detailed across the logging component and the IPC router interface. | 0 |
| `{GLOBAL_IndependentHeap}` | 🟢 PASS | The design sections are highly consistent with the definition of {GLOBAL_IndependentHeap}. The requirement for physical and logical isolation of memory domains to prevent fault propagation is consistently implemented across the OS core, system configuration, and platform memory layers. | 0 |
| `{GLOBAL_StrictMemoryLimit}` | 🟢 PASS | The keyword {GLOBAL_StrictMemoryLimit} is consistently applied across the architecture and component specifications. The definition of strict memory limits (e.g., 20KB/64KB) is concretely implemented through static configuration macros and formal invariants. | 0 |
| `{META_RestrictedPhysicalAccess}` | 🟢 PASS | The implementation of {META_RestrictedPhysicalAccess} is consistent across all referencing sections. The definition requiring strict physical resource access via permission tables is concretely realized through the `FB_CONF_VMMIO_ALLOWED_ADDRS` constexpr array and the vMMIO PTE-based permission gate. | 0 |
| `{GLOBAL_Policy_Memory}` | 🟢 PASS | The design sections consistently implement the {GLOBAL_Policy_Memory} requirement across all tiers, ensuring the prohibition of dynamic heap allocation (malloc/new) and the use of static/stack allocation or pool-based reuse. | 0 |
| `{GLOBAL_PeriodicTask}` | 🟡 WARN | The design generally aligns with the definition of {GLOBAL_PeriodicTask}, but there is a critical gap in the OS Scheduler's implementation details regarding how periodic tasks are actually triggered and managed. | 2 |
| `{MemoryBoundaryCheck}` | 🟡 WARN | The requirement {MemoryBoundaryCheck} is generally well-integrated across the interpreter and runtime, but there is a critical semantic contradiction in the JIT compiler section regarding the definition of 'Boundary Check'. | 2 |
| `{ROMParsing}` | 🟢 PASS | The design sections consistently implement the {ROMParsing} requirement. The 'Zero Copy Loading' mandate is fully reflected through the use of ModuleView, BinaryStream (using std::span), and proxy accessors that decode data directly from ROM without RAM expansion. | 0 |
| `{Challenge_ApproximateYield}` | 🟢 PASS | The requirement {Challenge_ApproximateYield} is consistently implemented across the design sections. The definition of 'trace-count based approximate yield' is correctly reflected in the ADR, the interpreter algorithm, and the vSoC engine's state transitions. | 0 |
| `{GLOBAL_InterruptWakeup}` | 🟡 WARN | The design generally implements the requirement, but there is a conceptual contradiction regarding the notification model between the OS core and the HAL layer. | 2 |
| `{GLOBAL_StaticScalability}` | 🟢 PASS | The keyword {GLOBAL_StaticScalability} is consistently applied across all referencing design sections. The definition requiring resource limits to be determined as compile-time constants to eliminate dynamic overhead is strictly followed through the use of macros, constexpr arrays, and static assertions. | 0 |
| `{Resource_Estimation_Model}` | 🟢 PASS | The Resource_Estimation_Model requirement is consistently and completely implemented across the design sections. The definition's mandate to estimate ROM/RAM footprints and verify constraint compliance is directly realized through a deterministic calculation model and compile-time static_asserts. | 0 |
| `{Challenge_CspHandoffStarvation}` | 🟢 PASS | The requirement {Challenge_CspHandoffStarvation} is consistently addressed across the design sections. The risk of starvation is mitigated through both a hard limit on consecutive handoffs and a time-slice threshold, with corresponding formal verification methods defined. | 0 |
| `{MemoryIsolation}` | 🟢 PASS | The referencing design sections are consistent with the definition of {MemoryIsolation}. The design implements the requirement through static memory pools, ring buffers, and memory partitioning, aligning with the goal of hardware or logical boundary isolation. | 0 |
| `{VDMA}` | 🟢 PASS | The design sections consistently implement the {VDMA} requirement. The definition of high-speed transfer between guest linear memory and virtual/physical addresses is fully realized through the VDMA register set, the system syscall wrapper, and the vMMIO memory map. | 0 |
| `{LowLatencyLookup}` | 🟢 PASS | The design sections are perfectly consistent with the definition of {LowLatencyLookup}. The requirement for O(log N) complexity via sorted arrays and binary search is explicitly implemented and documented across the IPC Router's algorithms, pipeline, state machine, and performance strategies. | 0 |
| `{IPC_ZeroCopy}` | 🟢 PASS | The design sections fully implement and align with the {IPC_ZeroCopy} requirement. The mechanism for eliminating data copying is explicitly detailed through a three-stage ownership transfer process (Revoke -> Enqueue -> Grant) and the use of relative offset pointers. | 0 |
| `{IPC_DropHandler}` | 🟢 PASS | The requirement {IPC_DropHandler} is consistently and comprehensively implemented across all referencing design sections. The definition as an 'In-flight resource recovery Drop handler' is accurately reflected in the algorithm, state machine, lifecycle descriptions, and formal verification invariants. | 0 |
| `{MultiModule_Support}` | 🟢 PASS | The design sections consistently implement the requirements for {MultiModule_Support}. The definition's requirement for loading multiple modules and dynamic linking is fully addressed by the 'Module Registry' for management and the 'Dynamic Linking Sequence' for symbol resolution and patching. | 0 |
| `{JIT_Safepoint}` | 🟢 PASS | The design sections are highly consistent with the definition of {JIT_Safepoint}. The requirement for asynchronous interrupt checkpoints is fully elaborated through a state machine, specific implementation points (loop back-edges, function calls), a defined flag structure, and formal verification properties. | 0 |
| `{Debugger_Jit_Flush}` | 🟢 PASS | The requirement {Debugger_Jit_Flush} is consistently and completely implemented across the design sections. The definition of 'JIT cache flush during intervention' is detailed through a specific trigger mechanism, a state transition model, and formal verification invariants. | 0 |
| `{Challenge_JITCacheEfficiency}` | 🟢 PASS | The design sections are highly consistent and complete regarding the {Challenge_JITCacheEfficiency} requirement. The proposed 3-bank multi-buffer strategy directly addresses the memory constraints and efficiency goals defined in the requirement list. | 0 |
| `{GLOBAL_UseCpp20Coroutine}` | 🟢 PASS | The keyword {GLOBAL_UseCpp20Coroutine} is consistently applied across the definition and design sections. The design sections correctly implement the requirement by specifying a stackless, low-overhead task switching mechanism using C++20/23 coroutines, and the traceability is maintained throughout the OS and scheduler specifications. | 0 |
| `{CooperativeMultitasking}` | 🟢 PASS | The requirement {CooperativeMultitasking} is consistently implemented across the design sections. The definition of a custom cooperative OS using coroutines is reflected in the core OS concept, the scheduler's mechanism, the system call interface (SYS_YIELD), and the IRQ handling logic. | 0 |
| `{CSPCommunication}` | 🟢 PASS | The design sections consistently implement the {CSPCommunication} requirement. The definition of Hoare CSP with zero-copy ownership transfer is accurately reflected across the OS core, scheduler, and system call specifications. | 0 |
| `{Asynchronous_Notification}` | 🟢 PASS | The design sections consistently implement the requirement for asynchronous notifications using WASI pollables and virtual interrupts. | 0 |
| `{PositionIndependentCode}` | 🟢 PASS | The requirement {PositionIndependentCode} is consistently applied across the design sections. The definition specifies that output binaries must be PIC, and the design sections correctly implement this by ensuring the constexpr assembler generates PIC-compliant instructions and the JIT compiler utilizes PIC to ensure placement flexibility and security (W^X). | 0 |
| `{JIT_RuntimeAPI_Fallback}` | 🟢 PASS | The requirement {JIT_RuntimeAPI_Fallback} is consistently implemented across the interpreter and JIT engine design sections. The design correctly translates the high-level requirement of reducing engine complexity into specific architectural decisions: a unified function signature for handlers and the offloading of complex operations to helper functions. | 0 |
| `{PhysicalPassthrough}` | 🟡 WARN | The design sections reference {PhysicalPassthrough} for various HAL functions, but there is a conceptual gap between the 'Direct Physical Access' definition and the 'IPC-based' implementation described in the HAL sections. | 3 |
| `{URIAbstraction}` | 🟢 PASS | The keyword {URIAbstraction} is consistently applied across all referencing design sections. The definition specifies a URI format for loose coupling, and the design sections correctly integrate this as a foundation for DI, IoC, and service discovery across different architectural tiers. | 0 |
| `{NativeAPI_Export}` | 🟢 PASS | The design sections are highly consistent with the definition of {NativeAPI_Export}. The requirement for 'minimal trap instructions' and 'vMMIO' is explicitly implemented via the 'fireball_call' mechanism and vMMIO register mapping. | 0 |
| `{CSP_Handoff}` | 🟡 WARN | The design generally implements the CSP_Handoff requirement, but there is a critical contradiction regarding the execution path (Scheduler-mediated vs. Scheduler-bypass). | 2 |
| `{FastAddressCheck}` | 🟢 PASS | The requirement {FastAddressCheck} is consistently implemented across the design sections. The definition of using bitwise operations for fast boundary checks is explicitly detailed in the runtime implementation and supported by the system configuration macros. | 0 |
| `{JIT_OldestOnly_Promote}` | 🟢 PASS | The requirement {JIT_OldestOnly_Promote} is consistently and completely implemented across all referencing design sections. | 0 |
| `{RSPMinimalSet}` | 🟡 WARN | The design sections correctly reference the requirement and establish the architectural flow, but the 'minimal set' of GDB RSP commands is not actually defined. | 1 |
| `{RSP_Transport_Selectable}` | 🟢 PASS | The requirement {RSP_Transport_Selectable} is consistently and completely implemented across the design sections. | 0 |
| `{Debug_Integrated}` | 🔴 FAIL | The design sections fail to implement the core functional requirements of {Debug_Integrated}, specifically the integration of a profiler and dynamic testing tools. | 2 |
| `{EnvironmentPointer}` | 🟢 PASS | The design sections are fully consistent with the definition of {EnvironmentPointer}. The requirement for type-safe access to peripheral components via a 'vsoc_runtime*' pointer is explicitly implemented in the execution context and the vSoC concept. | 0 |
| `{Interpreter_LazyJITSwitch}` | 🟢 PASS | The requirement {Interpreter_LazyJITSwitch} is consistently implemented across the design sections. The definition's goal of dynamic native transition via JIT cache re-evaluation during interpreter loop returns is explicitly detailed in the algorithm section and supported by the sequence diagram and backlog. | 0 |
| `{LightweightVerifier}` | 🟢 PASS | The design sections are consistent with the definition of {LightweightVerifier}. The requirement for a fast, minimal check (magic value, version) is correctly implemented across the state machine, safety constraints, and the implementation backlog. | 0 |
| `{vMMIO_TrapAndEmulate}` | 🟢 PASS | The requirement {vMMIO_TrapAndEmulate} is consistently implemented across the design sections. The definition of trapping guest memory access to call host hooks is directly realized through the 'register-hook' mechanism and the vMMIO architectural layer. | 1 |
| `{WasmPageAlignment}` | 🟢 PASS | The requirement {WasmPageAlignment} is consistently implemented and referenced across all design sections. The definition of 64KB page alignment is strictly maintained in the platform memory allocation and MPU configuration. | 0 |
| `{JIT_Encoder}` | 🟢 PASS | The design sections consistently implement the {JIT_Encoder} requirement. The use of C++ constexpr for build-time instruction template generation is explicitly detailed across multiple architecture-specific implementations (RISC-V and ARM) and the overall JIT component decomposition. | 0 |
| `{JIT_LazyChaining}` | 🟢 PASS | The design sections consistently implement the {JIT_LazyChaining} requirement. The definition's goal of reducing search overhead by defaulting to the interpreter is explicitly detailed in the design as the 'initial state' of the chaining slot and the use of dispatcher stubs. | 0 |
| `{Challenge_InterruptSafety}` | 🟢 PASS | The design sections consistently implement the requirements for {Challenge_InterruptSafety}. The transition from a 'Pending' status in the definition to a 'Decided' status in the ADR and detailed implementation in the Runtime and HAL sections is coherent. | 0 |
| `{META_SpecificationFirst}` | 🟢 PASS | The keyword {META_SpecificationFirst} is consistently applied across all referencing design sections. The definition 'development stance of defining formal specifications or contracts prior to implementation' is strictly followed by the project's phase structure, which mandates a 'Specification First' approach (Phase 0) and a formal 'GO/NO-GO' gate before transitioning to the C++23 implementation phase. | 0 |
| `{META_BumpAllocator}` | 🟢 PASS | The referencing design sections are consistent with the definition of {META_BumpAllocator}. The implementation details in the runtime loader (save/restore for transaction protection and LIFO constraints for unloading) correctly align with the nature of a bump allocator to prevent fragmentation and ensure high-speed allocation. | 0 |
| `{Errorcode_To_Strategy}` | 🟢 PASS | The design sections consistently implement the requirement {Errorcode_To_Strategy} by defining a clear mechanism to map low-level errno values to high-level recovery strategies. | 0 |
| `{WASI_Implementation}` | 🟢 PASS | The implementation of {WASI_Implementation} is consistent across the provided sections. The design successfully bridges the high-level WIT interface definitions with the low-level system call mapping. | 0 |
| `{TypeSafeMessaging}` | 🟢 PASS | The design sections consistently implement the requirements defined for {TypeSafeMessaging}, specifically the use of a static flat map for O(log N) search efficiency and the avoidance of dynamic memory allocation. | 0 |
| `{ContextPointerRegister}` | 🔴 FAIL | The design fails to implement the core requirement of the {ContextPointerRegister} definition. | 2 |
| `{ZeroCopyIndexing}` | 🟢 PASS | The design sections consistently implement the ZeroCopyIndexing requirement. The definition specifies 'Zero-copy indexing of WASM sections by the Loader', and the design section 4.1 explicitly details the mechanism: avoiding RAM copies of section contents, using ROM offsets/sizes, and utilizing std::string_view for export names to ensure zero RAM copying. | 0 |
| `{TaskPollInterruptFlag}` | 🟡 WARN | The design implements the notification mechanism, but there is a conceptual ambiguity regarding 'polling' versus 'wakeup'. | 1 |
| `{CleanArchitecture}` | 🟡 WARN | The design references the Clean Architecture principle, but the provided BDD (Block Definition Diagram) shows dependency directions that potentially contradict the 'dependency towards the interior' rule. | 2 |
| `{META_ZeroOverhead}` | 🟢 PASS | The keyword {META_ZeroOverhead} is consistently applied across the referencing design sections, aligning with its definition as 'Zero-cost abstraction for high-performance embedded C++ design'. | 0 |
| `{META_AI_Native_Dev}` | 🟢 PASS | The keyword {META_AI_Native_Dev} is consistently used across the definition and referencing sections. It serves as a high-level development policy rather than a technical constraint, and its application in the design and planning sections is appropriate. | 0 |
| `{META_Risk_Tiering}` | 🟡 WARN | The keyword {META_Risk_Tiering} is used as a traceability tag in multiple design and planning sections, but there is no concrete evidence of its application (e.g., defined risk tiers or adjusted verification levels) in the referencing sections. | 1 |
| `{META_NoStdVector}` | 🟢 PASS | The referencing design sections are fully consistent with the definition of {META_NoStdVector}. All instances correctly replace dynamic vectors with fixed-length arrays or custom allocation strategies. | 0 |
| `{COOS_Transparent}` | 🟢 PASS | The design sections consistently implement the requirement for task state visualization through the introduction of a State Visualizer Interface and the inclusion of state tracking in the task_context. | 0 |
| `{vMMIO_Isolation}` | 🟢 PASS | The requirement {vMMIO_Isolation} is consistently implemented across the design sections. The definition's goal of ensuring memory safety by restricting device I/O to the vMMIO space is supported by the configuration of a dedicated base address (0x80000000) and the implementation of a page table (FlatMap) to manage these isolated mappings. | 0 |
| `{UnifiedAccessModel}` | 🟢 PASS | The design sections consistently implement the UnifiedAccessModel requirement by centralizing all external memory and I/O access through the vMMIO layer. | 0 |
| `{Trap_Interface}` | 🟢 PASS | The design sections are fully consistent with the definition of {Trap_Interface}. The requirement for a 'trap instruction-based synchronous communication interface for the fast path' is explicitly implemented and detailed through the 'fireball_call' mechanism, register mapping, and the synchronous control flow described in the design sections. | 0 |
| `{Type_Vocabulary}` | 🟢 PASS | The design sections strictly adhere to the {Type_Vocabulary} requirement by defining a concrete set of type aliases and a vocabulary set to map semantic meanings to physical u32 representations. | 0 |
| `{IPC_HandleBased}` | 🟢 PASS | The design sections consistently implement the {IPC_HandleBased} requirement. The transition from URI-based lookup to handle-based communication is explicitly defined in the syscalls and the IPC router logic. | 0 |
| `{Fast_Path_GPIO}` | 🟡 WARN | The design implements the Fast_Path_GPIO requirement through two different mechanisms (Direct Syscall and vMMIO), which creates a conceptual ambiguity regarding the 'single' fast path implementation. | 2 |
| `{Syscall_Mapping}` | 🟢 PASS | The design sections consistently implement the {Syscall_Mapping} requirement by defining the bridge between WASM guest calls (WIT) and host-side system call IDs. | 0 |
| `{IPCRegistry}` | 🟢 PASS | The design sections are consistent with the definition of {IPCRegistry}. The implementation details (static_flat_map, O(log N) lookup, and URI mapping) directly align with the requirements. | 0 |
| `{JIT_ReverseCompilationOrder}` | 🟢 PASS | The design sections consistently implement the requirement for reverse compilation order to improve chaining rates. | 0 |
| `{IPCDI}` | 🟢 PASS | The requirement {IPCDI} is consistently referenced and integrated across the architecture overview and the IPC router design sections. | 0 |
| `{IoC}` | 🟢 PASS | The requirement {IoC} is consistently referenced and implemented across the design sections. The definition specifies the Dependency Inversion Principle, which is explicitly applied in the IPC Router's service facade to decouple the service consumer from the underlying IPC primitives. | 0 |
| `{ConceptHarnessDI}` | 🟢 PASS | The implementation of {ConceptHarnessDI} is consistent across the definition, architectural decision, and component specification. | 0 |
| `{GLOBAL_UseCpp23Library}` | 🟢 PASS | The referencing design sections are fully consistent with the definition of {GLOBAL_UseCpp23Library}. | 0 |
| `{Size_15KLOC}` | 🟢 PASS | The requirement {Size_15KLOC} is consistently referenced and tracked across the design sections without contradictions. | 0 |
| `{DirectContextSwitch}` | 🟢 PASS | The requirement {DirectContextSwitch} is consistently and completely implemented across the design sections. The design specifies the technical mechanism (Symmetric Transfer) to achieve the low-latency goal defined in the requirements. | 0 |
| `{COOS_Scheduling_Refine}` | 🟢 PASS | The referencing design sections are consistent with the definition of {COOS_Scheduling_Refine}. The requirement for continuous improvement and optimization is concretely implemented via ADR-SCHED-002, which defines a specific high-performance O(1) dispatch strategy. | 0 |
| `{Challenge_CoosBlockedList}` | 🟢 PASS | The design sections consistently address the challenge defined in the requirement list. The trade-off between management cost and real-time performance is explicitly resolved via an event-driven queue structure. | 1 |
| `{Challenge_DebuggerResource}` | 🟡 WARN | The design sections reference the challenge, but the specific constraints regarding JIT coexistence and memory limits are not explicitly addressed in the implementation details. | 2 |
| `{WIT_Interface_Purpose}` | 🟡 WARN | The referencing design sections provide functional purposes but fail to describe the 'logical invariants' explicitly required by the definition. | 1 |
| `{WASI_Async_Bridge}` | 🟢 PASS | The referencing design sections are consistent with the definition of {WASI_Async_Bridge}. The design explicitly details the mechanism (co_yield and wait_for_ipc_response) used to bridge synchronous WASI calls to asynchronous IPC, fulfilling the high-priority requirement. | 0 |
| `{ConsolidatedHeap}` | 🟢 PASS | The design sections are fully consistent with the definition of {ConsolidatedHeap}. The requirement to manage physical memory as a single unified pool to maximize efficiency is correctly implemented as the foundation from which isolated partitions ({GLOBAL_IndependentHeap}) are carved. | 0 |
| `{DebuggerLabelTableSwitch}` | 🟡 WARN | The design references the requirement and explains the 'off' state behavior, but fails to specify the actual mechanism or content of the 'debug-use' handler table. | 1 |
| `{Debug_Standard_Env}` | 🟢 PASS | The design sections are consistent with the requirement {Debug_Standard_Env}. The supported tools (VSCode, UART, J-Link) are explicitly mentioned in the concept section, and the implementation mechanism (attach function via HAL transport) aligns with the requirement to support these environments. | 0 |
| `{Wasm32Only}` | 🟢 PASS | The referencing design sections are consistent with the definition of {Wasm32Only}. | 0 |
| `{JIT_RegisterMapping}` | 🟢 PASS | The requirement {JIT_RegisterMapping} is consistently implemented and referenced across the design sections. | 0 |
| `{ZeroRuntimeOverhead}` | 🟢 PASS | The design sections consistently implement the {ZeroRuntimeOverhead} requirement by utilizing C++ constexpr for static resolution, ensuring that abstraction costs are paid at compile-time rather than runtime. | 0 |
| `{JIT_ZeroCompileCostTheorem}` | 🟢 PASS | The design sections are fully consistent with the definition of {JIT_ZeroCompileCostTheorem}. The requirement to achieve compilation speeds so fast that optimization is unnecessary is correctly implemented via the 'Copy-and-Patch' strategy, which offloads optimization to build-time. | 0 |
| `{HAL_Interface}` | 🟡 WARN | The design sections implement the functional aspect of the HAL interface, but there is a discrepancy regarding the transport mechanism specified in the definition. | 2 |
| `{LowOverhead}` | 🟢 PASS | The referencing design section is consistent with the definition of {LowOverhead}. | 0 |
| `{ServiceSelfReboot}` | 🟡 WARN | The design section acknowledges the requirement for service self-reboot, but the implementation mechanism is underspecified compared to the definition. | 1 |
| `{FaultTolerant}` | 🟢 PASS | The referencing design section is consistent with the definition of {FaultTolerant}. | 0 |
| `{SelfReboot_via_Event}` | 🟢 PASS | The requirement {SelfReboot_via_Event} is consistently and completely implemented in the referencing design section. | 0 |
| `{IPC_Resource_Isolation}` | 🟡 WARN | The design references the requirement for IPC resource isolation, but the implementation details are insufficient to verify 'complete separation and protection' as demanded by the definition. | 1 |
| `{META_ZeroCostAbstraction}` | 🟢 PASS | The referencing design section is fully consistent with the definition of {META_ZeroCostAbstraction}. | 0 |
| `{META_CompileTimeValidation}` | 🟢 PASS | The referencing design section is fully consistent with the definition of {META_CompileTimeValidation}. | 0 |
| `{META_BinarySearch}` | 🟢 PASS | The referencing design section is consistent with the definition of {META_BinarySearch}. | 0 |
| `{EliminateDataRace}` | 🟢 PASS | The design section is fully consistent with the requirement definition for {EliminateDataRace}. | 0 |
| `{NotRTOS}` | 🟢 PASS | The design section is consistent with the requirement to prioritize memory efficiency and portability over real-time performance. | 0 |
| `{COOS_Deterministic}` | 🟡 WARN | The design section acknowledges the requirement for deterministic execution but fails to specify the mechanism for limiting context switches to explicit points. | 1 |
| `{LowOverheadSwitch}` | 🔴 FAIL | The referencing design section fails to implement or describe the technical mechanisms required to achieve the LowOverheadSwitch requirement. | 2 |
| `{WIT_Interface_Spec}` | 🟢 PASS | The design section correctly implements the requirement for a language-independent interface definition using WebAssembly Interface Types (WIT). | 0 |
| `{Syscall_Return_Value}` | 🟢 PASS | The design section correctly implements the requirement for system call return value standards and error propagation. | 0 |
| `{Challenge_WasiFdWriteLoop}` | 🟢 PASS | The design section correctly addresses the challenge defined in the requirement list, providing a concrete implementation strategy for the WASI fd_write loop. | 0 |
| `{Challenge_SyscallMemorySafety}` | 🟢 PASS | The design section correctly references the challenge and provides a specific implementation strategy that aligns with the definition. | 0 |
| `{WIT_First}` | 🟢 PASS | The referencing design section correctly aligns with the WIT_First requirement by establishing the WIT interface as the primary definition for system calls and HAL. | 0 |
| `{WIT_Common_Types}` | 🟡 WARN | The referencing section acknowledges the requirement via traceability tags, but fails to provide the actual implementation or definition of the common types. | 1 |
| `{ServiceFacade}` | 🟡 WARN | The design section references the requirement but fails to specify the 'type-safe method' implementation mandated by the definition. | 1 |
| `{InterpreterContextStackless}` | 🟡 WARN | The design section references the requirement but fails to specify the technical implementation details of the 'stackless' mechanism. | 1 |
| `{DynamicMmap}` | 🟡 WARN | The design section references {DynamicMmap} as a conceptual driver for the vMMIO architecture, but it fails to specify the actual mechanism for 'temporary mapping' or 'shared memory ID' handling required by the definition. | 1 |
| `{vMMIO_TLB}` | 🟢 PASS | The referencing design section is fully consistent with the definition of {vMMIO_TLB}. | 0 |
| `{ADR_ScalableCodeOffset}` | 🟢 PASS | The design section correctly implements and expands upon the requirement defined in the ADR list without contradictions. | 0 |
| `{ADR_SafeQueuingOnHotMiss}` | 🟢 PASS | The design section for {ADR_SafeQueuingOnHotMiss} is consistent with the definition and provides a clear technical resolution to the stated challenge. | 0 |
| `{SinglePassCompilation}` | 🟢 PASS | The referencing design section is consistent with the definition of {SinglePassCompilation}. | 0 |
| `{HistoryBuffer}` | 🟡 WARN | The referencing design acknowledges the use of HistoryBuffer but fails to specify how the 'ring buffer' characteristic defined in the requirements is implemented or utilized. | 1 |