.syntax unified
.cpu cortex-m33
.thumb

.section .text
.global _start
.type _start, %function
_start:
    /* Print "Booted\n" via semihosting SYS_WRITE0 (0x04) */
    mov r0, #0x04
    ldr r1, =boot_msg
    bkpt 0xab

    /* Call main */
    bl main
    /* result is in r0, pass to exit */
    bl exit
1:  b 1b

.global exit
.type exit, %function
exit:
    /* Semihosting exit call */
    /* Many QEMU ARM models expect r1 to be the reason code directly if it's ADP_Stopped_ApplicationExit */
    mov r0, #0x18
    ldr r1, =0x20026
    bkpt 0xab
1:  b 1b

.align 4
boot_msg:
    .asciz "Booted\n"
