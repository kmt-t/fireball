.syntax unified
.cpu cortex-m33
.thumb

/* Minimal vector table for picolibc */
.section .text.init.enter
.global __interrupt_vector
__interrupt_vector:
    .word __stack
    .word _start
