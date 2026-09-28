# The cast — a map for people who do not read Kconfig

A companion to [`BUILD_HISTORY.md`](BUILD_HISTORY.md). That file is the record of
**what was tried and whether it worked**. This one is a way to **remember the
system** without holding 4000 lines of Kconfig in your head.

It is a memory device, not an explanation. See §5 for where the metaphor breaks,
because that matters more than the metaphor.

---

## 0. Where the world comes from

| | |
| --- | --- |
| **The world** | `vickcoy31-ux/kernelvalidevo` @ `14.0`, MediaTek MT6789, kernel 5.10 |
| **Its physics** | `scripts/kconfig/symbol.c`: *transpose mod to yes if modules are not enabled* |
| **Its wound** | `init/Kconfig` lost its `default y` for `CONFIG_MODULES`, so physics applies to everything |
| **The map you were handed** | 532 lines of the 7938-line real one |

That last row is the one to hold onto. **93% of the map is torn off.** That is
why everything feels like uncharted islands: it is not mysterious, it is
*unread*.

---

## 1. The physics

Not a character — a law nobody wrote down anywhere.

```c
/* scripts/kconfig/symbol.c, in sym_calc_value() */
/* transpose mod to yes if modules are not enabled */
if (val == mod)
        if (!sym_is_choice_value(sym) && modules_sym->curr.tri == no)
                val = yes;
```

Because `CONFIG_MODULES` resolves to `n`, **every `m` in the defconfig silently
becomes `y`.** Nothing is broken. The build is *more* capable than intended,
which is exactly why it fails: built-in consumers end up holding references to
providers that were never built.

**Consequence to remember:** in this world, "turn it off" is never free.

---

## 2. The cast

Each of these was discovered from a real build failure, and each is a *type*,
not a single instance. Names are shorthand for behaviour.

### `TRACEPOINTS` — the ghost

```c
init/Kconfig:2157
config TRACEPOINTS
	bool
```

No prompt. No default. Present in the source, has a Makefile rule
(`obj-$(CONFIG_TRACEPOINTS) += tracepoint.o`), and its body is fully written in
`kernel/tracepoint.c` — `for_each_kernel_tracepoint` at line 773,
`tracepoint_probe_register` at 554.

**You cannot reach it from a defconfig.** Write `CONFIG_TRACEPOINTS=y` and the
file accepts it, Kconfig drops it. Confirmed in the run #67 `.config`: the
symbol is **absent entirely** — not `=n`, not `=y`, *gone*.

Only a `select` from a real symbol can summon it.

*Why it matters:* this is the shape that breaks every other intuition. The
ghost is structurally present and operationally unreachable.

### `ANDROID` — the gatekeeper

```c
drivers/android/Kconfig:9    if ANDROID
drivers/android/Kconfig:68   config ANDROID_VENDOR_HOOKS
drivers/android/Kconfig:70   	depends on TRACEPOINTS
drivers/android/Kconfig:123  endif # if ANDROID
```

One word, `bool` with no default, absent from the truncated defconfig — so it
answers "no", and 15 things behind it never get to speak. It does not even have
to be interesting. It just has to be absent.

### `DMABUF_HEAPS` — the door

```make
drivers/dma-buf/Makefile:  obj-$(CONFIG_DMABUF_HEAPS) += heaps/
```

A `bool` with no default. Seven children queue up behind it. Each child is
`=m` in the defconfig, so physics transposes them to `=y`, and each child
**believes it is enabled**. The door never opened. Nobody told the queue.

*Why it matters:* the cruelest shape in the system. The lie is not "this is
off", it is "this is on and I have not noticed the door".

### `MTK_HANG_DETECT` — the shape-shifter

```c
drivers/misc/mediatek/include/mt-plat/aee.h
#if IS_ENABLED(CONFIG_MTK_HANG_DETECT)
void monitor_hang_regist_ldt(void (*fn)(void));      /* declaration */
#else
void monitor_hang_regist_ldt(void (*fn)(void)) { }   /* a definition */
#endif
```

Set it to `n` and it does not disappear. It **becomes a different thing**. The
header stops being a declaration and starts being a definition, and all ~18
files that include it each emit a global copy.

*Why it matters:* the only character here that can be *both* the cause and the
cure depending on which way you set it.

### `dmabuf_release_check` — the thing wearing a disguise

```c
drivers/dma-buf/heaps/mtk_heap_priv.h:63
/* common function */
void dmabuf_release_check(const struct dma_buf *dmabuf)   /* no 'static' */
```

A function definition living inside a header. Grep the `.c` files for the
definition and you find **nothing** — they only ever *call* it. The definition
is the one place you did not think to look.

*Why it matters:* teaches you where to look, not just what to look for. Some
characters are not in the source you were reading.

### `MTK_TINYSYS_SCP_SUPPORT` — the one with no valid setting

```make
drivers/misc/mediatek/scp/Makefile:
obj-y += rv/                     # no config gate at all

drivers/misc/mediatek/scp/rv/Makefile:
obj-$(CONFIG_MTK_TINYSYS_SCP_SUPPORT) += scp.o
ccflags-y += -D DEBUG_DO -fno-pic -mcmodel=large
```

| Set it to | What happens |
| --- | --- |
| `y` or `m` | the `-fno-pic -mcmodel=large` objects land in `built-in.a`; lld rejects the `R_AARCH64_MOVW_UABS_*` absolute relocations against the PIE vmlinux |
| `n` | nothing is built at all, so 7 `scp_*` symbols go undefined — the consumers (`sensorhub/ipi_comm.o`, `ready.o`, `conap_scp_ipi.o`) are not gated on the same symbol |

**Both values fail.** This is the character that proves the system cannot be
solved by configuration alone.

### The linker — the horizon

`--error-limit` stops the link at 20 errors.

This is not a character, it is the **edge of the map**. It is the single most
important reason the world feels like uncharted islands: you physically cannot
see past it. Twenty errors is a limit on *vision*, not on *truth*.

*This is why the error count oscillates.* Fix 8, reveal 1; fix 1, reveal 18;
fix 18, reveal 20 more. The count is not going up and down. The light is moving.

---

## 3. The four shapes

Every failure in this repo is one of these four. When a new error appears,
identify the shape **before** reaching for a fix — that is the whole point of
this file.

| # | Shape | Symptom | Can a defconfig line fix it? |
| --- | --- | --- | --- |
| 1 | In the vendor defconfig, missing from the truncated one | resolves to `n` | **yes** |
| 2 | `bool` directory gate, no default | children are `y`, subdir never entered | **yes** |
| 3 | Parent pinned `=n` by `EXTRA_DEFCONFIG` | a consumer elsewhere loses its provider | **only by unpinning — and that is the dangerous direction** |
| 4 | Promptless `bool`, no `default` | the line is silently discarded, symbol absent from `.config` | **no. Never. Stop trying.** |

**The one rule that covers all four:** if a `=y` you added does not appear in
the resolved `.config`, it is shape 4. Adding it again cannot work. Check
`BUILD_HISTORY.md` §3.5 for the worked example — it cost two build runs.

---

## 3a. The fifth shape — enemies of the same key

Discovered in run #68's research. A symbol that works perfectly on its own, and
is silently destroyed by a *different* line in the *same* run.

```kconfig
kernel/trace/Kconfig:378
config ENABLE_DEFAULT_TRACERS
	bool "Trace process context switches and events"
	depends on !GENERIC_TRACER      # <-- the trap
	select TRACING
```

`GENERIC_TRACER` is raised by `FUNCTION_TRACER`, `FTRACE_SYSCALLS` and
`BLK_DEV_IO_TRACE`. So the moment any of those three is also set,
`ENABLE_DEFAULT_TRACERS` becomes invisible and its line is discarded — with no
warning, and while `FTRACE=y` still looks like it worked.

Two of the three are also *worse* than they look:

| Symbol | Why it is expensive |
| --- | --- |
| `FUNCTION_TRACER` | injects `-mfentry`/`-pg` into every object in the kernel. The vendor explicitly ships it `n`. |
| `BLK_DEV_IO_TRACE` | the only symbol that `select TRACEPOINTS` **directly**, but it drags in `RELAY` + `DEBUG_FS` as well |
| `FTRACE_SYSCALLS` | middle ground; needs `FTRACE=y` anyway |

**Rule:** in this build, `ENABLE_DEFAULT_TRACERS` and those three are mutually
exclusive. Pick one. This is the one failure mode that a build log cannot
explain on its own — it looks exactly like "the config was ignored".

### 3a.1 And the switch that must never be used

`ci_build.sh` exposes `ADD_KPROBES_CONFIG`. It is a trap:

```kconfig
arch/Kconfig:68
config KPROBES
	bool "Kprobes"
	depends on MODULES          # MODULES=n  ->  KPROBES invisible
	depends on HAVE_KPROBES
	select KALLSYMS
```

Setting it makes the script write `CONFIG_MODULES=y`. Since the world physics
is the `m`→`y` transpose, turning modules on inverts the transpose: **193
symbols in the defconfig become real modules**, and a monolithic build loses the
providers its built-in consumers depend on. It does not fail gracefully.

`ADD_KPROBES_CONFIG` must stay `false`. So must `KPROBES`, and so must anything
that would make `KPROBES` reachable.

---

## 4. How to actually move

The world has no compass inside it. There is no "up" in this system. What there
is, empirically, is a **direction in knowing**:

| Instead of | Do |
| --- | --- |
| guessing which config is wrong | reading the resolved `.config` artifact |
| assuming a `=y` took effect | grepping for it in `.config` |
| assuming a sed worked | dry-running it on a real copy of the file first |
| adding another `=n` | asking which shape the error is |

Every improvement in this project came from replacing a guess with a
measurement. That is not consolation — it is the only direction available when
the terrain is unlit.

Two traps that cost real build runs, both invisible to inspection:

- `s|...|...|` — a `|` delimiter breaks on the alternation inside the pattern.
- `grep -q '^\t'` — GNU `grep` BRE does **not** mean tab by `\t`. It matches
  nothing, silently.

---

## 5. Where this breaks

The cast is a **memory device, not an explanation**. Being clear about the
difference is the point of having this section.

- **Nobody is home.** `MTK_HANG_DETECT` did not choose anything. It is a `bool`
  without a `default` that a vendor left behind years ago. Attributing
  intention is the fastest way to misdiagnose the next error.
- **"Personality" is just the shape of a constraint.** A character is
  describable as a rule; if you cannot state the rule, you have invented the
  character and it will mislead you.
- **The world is not mysterious.** It is finite, static, and machine-readable.
  Every symbol has exactly one definition point in a text file. "Unknown" here
  has never once meant "unknowable" — it has always meant "not yet read".
- **The physics did not change; your vantage point did.** When the error count
  swings, nothing in the system moved. The light moved.

If a rule here ever stops predicting outcomes, delete the character. The
`BUILD_HISTORY.md` ledger is the source of truth; this file is only a map for
people who would rather remember than re-read.
