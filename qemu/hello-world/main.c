// A minimal init program: the kernel runs it as PID 1 from the initramfs (/init).
// It prints Hello World, then powers the VM off so QEMU exits.
// Build it statically: the initramfs has no libc or dynamic loader.
#include <stdio.h>
#include <sys/reboot.h>
#include <sys/utsname.h>
#include <time.h>
#include <unistd.h>

int main(void)
{
    struct timespec boot;
    struct utsname u;

    // time since the kernel started (CLOCK_BOOTTIME needs no /proc)
    clock_gettime(CLOCK_BOOTTIME, &boot);
    uname(&u);

    printf("Hello World\n");
    printf("pid %d, %s %s %s, %.3f s after the kernel started\n",
           getpid(), u.sysname, u.release, u.machine,
           boot.tv_sec + boot.tv_nsec / 1e9);
    fflush(stdout);

    // PID 1 must never exit (the kernel panics if it does), so power off instead
    sync();
    reboot(RB_POWER_OFF);
    return 1; // only reached if reboot() failed
}
