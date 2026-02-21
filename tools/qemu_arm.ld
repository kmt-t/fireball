/* Comprehensive linker script for QEMU ARM Cortex-M33 testing */
MEMORY
{
  ROM (rx) : ORIGIN = 0x10000000, LENGTH = 4M
  RAM (rwx) : ORIGIN = 0x38000000, LENGTH = 4M
}

STACK_SIZE = 0x4000;

SECTIONS
{
  .vector_table ORIGIN(ROM) :
  {
    LONG(_stack_top);
    LONG(_start | 1);
  } > ROM

  .text :
  {
    _stext = .;
    *(.text*)
    *(.glue_7*)
    *(.glue_7t*)
    *(.eh_frame)
    KEEP (*(.init))
    KEEP (*(.fini))
    _etext = .;
  } > ROM

  .rodata :
  {
    *(.rodata*)
    *(.gnu.warning*)
    _erodata = .;
  } > ROM

  .ARM.extab :
  {
    *(.ARM.extab* .gnu.linkonce.armextab.*)
  } > ROM

  .ARM.exidx :
  {
    *(.ARM.exidx* .gnu.linkonce.armexidx.*)
  } > ROM

  .data : AT(LOADADDR(.ARM.exidx) + SIZEOF(.ARM.exidx))
  {
    _sdata = .;
    *(.data*)
    *(.init_array*)
    *(.fini_array*)
    _edata = .;
  } > RAM

  .bss :
  {
    _sbss = .;
    __bss_start__ = .;
    *(.bss*)
    *(COMMON)
    _ebss = .;
    __bss_end__ = .;
  } > RAM

  PROVIDE(end = .);

  /* Set stack top at end of RAM */
  _stack_top = ORIGIN(RAM) + LENGTH(RAM);
}
