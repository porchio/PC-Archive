# Olivetti MX-08 — Firmware Deep Dive

*Reverse-engineering notes for the ROM dump `Olivetti_MX08.bin`.*
*Working notebook for the blog series — facts are flagged as **confirmed** (provable
from the bytes) or **inferred** (best reading of the evidence).*

---

## 0. TL;DR

The MX-08 ROM is a **Z80** firmware for a serial **point-of-sale controller**. It is not
the cash register's "brain" in the application sense — it is a **communications hub /
multiplexer** that sits between a host and a fan-out of serial peripherals (keyboard,
two customer/operator displays, a fiscal module, and a comms line). The board is built
around a **bank of Z80 SIO/DART serial controllers** driven by **mode-2 vectored
interrupts**, with software ring buffers feeding each channel.

| Property | Value | Evidence |
|---|---|---|
| CPU | Zilog Z80 @ **4 MHz** (8 MHz crystal ÷2) | 4 MHz-rated SIO/CTC ⇒ 4 MHz bus; `IM 2`, daisy-chain |
| Serial complement | **1× Z80 SIO/0 + 3× Z80 DART + 2× Z80 CTC** (5th SIO socket = expansion) | board markings (SIO/0 = `Z0844004PSC`) + I/O map §4 |
| Baud rates | 1200 / 2400 / 4800 / 9600 / 19200 (CTC @ 4 MHz, ÷16) | §6.7 |
| RAM | 62256, 32 KB SRAM (low 16 KB used) | board; RAM test §3 |
| ROM size | 32 KiB (27256 EPROM) | File is exactly 32768 bytes |
| Used | `0x0000–0x6580` (25,985 bytes); top 21 % is `0xFF` fill | Last non-`FF` byte at `0x6580` |
| Product name | **"ORS 500"** | ASCII at `0x0601` |
| Firmware version | **"I 2.1", dated 17 Nov 1992** | ASCII `"I 2.1 17nov92"` at `0x44FB` |
| Author credit | **"SALSI ADRIANO"** | ASCII at `0x0310` |
| 16-bit byte sum | `0x010D` | `sum(bytes) & 0xFFFF` |
| MD5 | `1884bc2c94e2dbf227d22f3964370217` | — |

---

## 1. Identity strings

A handful of plain-ASCII strings give us the machine's identity for free:

```
0x0310  "SALSI ADRIANO"                  ← programmer credit AND serial loopback test pattern (§7.1)
0x0601  "  O r S 500 "                   ← product banner
0x0610  "  O R S 500 (TEST)"             ← service / self-test banner
0x0639  "ROM,RAM ?"                      ← self-test prompt
0x0658  "ROM ?"                          ← ROM test label
0x0673  "RAM ?"                          ← RAM test label
0x067A  "MPX KEYB OPDS CLDS FISC C"      ← peripheral / channel name table
0x44FB  "I 2.1 17nov92"                  ← firmware version, transmitted on request
```

The **`MPX KEYB OPDS CLDS FISC C`** string at `0x067A` is the Rosetta Stone of this dump.
It is a table of short peripheral names, each terminated by a control byte. Decoded:

| Token | Meaning (inferred) |
|---|---|
| `MPX`  | Multiplexer (this board / the bus master) |
| `KEYB` | Keyboard |
| `OPDS` | **Op**erator **d**i**s**play |
| `CLDS` | **Cl**ient (customer) **d**i**s**play |
| `FISC` | **Fisc**al module / fiscal printer |
| `C`    | Comms / host channel |

These names line up one-for-one with the serial controllers found in the I/O map
(Section 4), which is the strongest single piece of evidence that this firmware's job is
to shuttle messages between a host and these five peripheral classes.

> **Why "ORS 500" and not "MX-08"?** "MX-08" is the Olivetti marketing/model number for
> the unit the EPROM was pulled from; "ORS 500" is the internal firmware identity the
> code reports. It's common for the silkscreen name and the ROM's self-reported name to
> differ. Treat both as the same device for the blog.

---

## 2. Memory map

The reset vector tells us the layout immediately:

```
0000  F3            DI                 ; interrupts off
0001  31 00 C0      LD SP,$C000        ; stack just below 0xC000  → RAM is up here
0004  CD 3C 01      CALL $013C         ; power-on settle delay (busy-wait, not chip init — see §3)
```

| Range | Contents | Confidence |
|---|---|---|
| `0x0000–0x7FFF` | **ROM** (only `0x0000–0x6580` used; rest is `0xFF`) | confirmed |
| `0x8000–0xBFFF` | **RAM** — a 62256 (32 KB SRAM) decoded into this 16 KB window only | confirmed (board) |
| `~0x9700` | a secondary stack (`LD SP,$9700` in self-test) | confirmed |
| `~0x99E0–0x9B00` | working variables, parser state, serial ring-buffer pointers | confirmed |
| `0xC000` | top of main stack | confirmed |
| `0xE000`, `0xF000` | **write-only hardware latches** (solenoid/relay strobes — cash drawer) | confirmed (§6.6) |

Serial state lives in this RAM window: TX ring ptrs `0x99EA/0x99EC`, RX ring ptrs
`0x99EE/0x99F0`, parser-state pointer `0x9A00`, decoded peripheral/command/param at
`0x9A46/0x9A47/0x9A48`, and a bank of **32-byte per-channel state blocks** from `0x9821`
upward (stride `0x20`).

The classic 8-bit split: **32 K ROM low, RAM high**. The fitted RAM is a **62256 (32 KB
SRAM)** but the board **decodes it into the 16 KB window `0x8000–0xBFFF` only**. The boot RAM
test (§3) walks exactly this range, the stack tops out just above at `0xC000` (growing down
into `0xBFFF`), and variables cluster around `0x9800–0x9B00`. Above the RAM, `0xC000–0xFFFF`
is free for the write-only latches at `0xE000`/`0xF000` (§6.6).

**The address decode (74LS139).** A single **74LS139** dual 2-to-4 decoder carves the 64 KB
space into four 16 KB pages from the top two address lines. One half is wired **A14 → A
(LSB), A15 → B (MSB), enable → Z80 `/MREQ`**, giving:

| Output (pin) | A15 | A14 | Page | Use |
|---|---|---|---|---|
| `Y0` (12) | 0 | 0 | `0x0000–0x3FFF` | ROM (low half) |
| `Y1` (11) | 0 | 1 | `0x4000–0x7FFF` | ROM (high half) |
| **`Y2` (10)** | **1** | **0** | **`0x8000–0xBFFF`** | **RAM `/CS`** |
| `Y3` (9) | 1 | 1 | `0xC000–0xFFFF` | latch / I-O region |

The 62256's RAM **`/CS` comes off pin 10 (`Y2`)** — active only when `A15=1, A14=0`. This is
why only 16 KB of the 32 KB part is usable: **all** the chip's address pins (including its own
A14) are tied straight to the bus, but `/CS` is only asserted while A14 is low, so the SRAM's
A14 is *always 0* whenever it is selected and the upper 16 KB of the die can never be reached.
Not a "partial address decode" trick on the chip — the *page decoder* simply hands RAM the one
page where A14 must be 0. (`/MREQ` on the enable keeps the SRAM off the bus during I/O cycles,
so the `0x00–0x6x` port reads/writes don't collide with it.)

**Second-level decode of the top page (`Y3` → another LS139).** The `0xC000–0xFFFF` output
(`Y3`) feeds the **enable of a second 74LS139**, whose select inputs are **A12 and A13**
(confirmed on the board), sub-dividing that 16 KB page into four 4 KB regions. The firmware
matches this exactly: the *only* genuine memory-mapped I/O it performs in the whole page is to
**`0xE000` and `0xF000`** (the write-only strobes of §6.6; every other `0xC…/0xF…` address in
the linear disassembly is a one-off inside a data/jump-table region — artifacts, not real
accesses). `0xE000` (`…1110`) and `0xF000` (`…1111`) share `A13=1` and differ only in `A12`,
so:

| 2nd-139 out | A13 | A12 | Range | Use |
|---|---|---|---|---|
| `Y0` | 0 | 0 | `0xC000–0xCFFF` | unused (spare / expansion?) |
| `Y1` | 0 | 1 | `0xD000–0xDFFF` | unused (spare / expansion?) |
| **`Y2`** | 1 | 0 | `0xE000–0xEFFF` | **`0xE000` strobe latch** |
| **`Y3`** | 1 | 1 | `0xF000–0xFFFF` | **`0xF000` strobe latch** |

The two used outputs select the strobe latches; the latch clock is the decoder output (likely
**gated with `/WR`**). On the board these are a **74LS174 (hex D-FF) + 74LS175 (quad D-FF)** —
clocked D-type registers on the data bus, **10 latched output bits total**. The firmware drives
two of them — **Drawer A** (`0xE000`) and **Drawer B** (`0xF000`) — and the remaining bits feed
other control loads (relays / status / mode lines). `Y0`/`Y1` (`0xC000–0xDFFF`) are unused by
the firmware and are the natural place for the expansion connector or a future input port —
*worth tracing where they go.*

`RST $38` (opcode `0xFF`) is **not** used as a real call — every `RST 38` in the linear
disassembly is just blank `0xFF` EPROM being decoded as instructions. Ignore them.

---

## 3. Boot / reset flow

```
0000  DI
0001  LD SP,$C000
0004  CALL $013C            ; power-on settle delay (busy-wait, NOT chip init)
0007  LD A,$10
0009  OUT ($43),A           ; SIO@0x40 ch.B WR0 = 0x10 (reset ext/status latches)
000B  IN  A,($43)           ; read RR0 (status) back
000D  BIT 3,A               ; RR0 bit 3 = DCD status on SIO/0 ch.B (the keyboard channel)
000F  JP  Z,$031E           ; bit3 = 0 (no-carrier) → SELF-TEST / service mode
0012  LD SP,$C000           ; bit3 = 1 (carrier present) → normal operation
0015  DI
0016  ...                   ; program all serial controllers (§3.1), then self-check (§3.2)
```

**Key insight:** the firmware decides *test vs. normal* by reading the **DCD line of the SIO/0
channel B** (port `0x43`) right after reset (`BIT 3,A` on `RR0`). `RR0 bit 3 = 0` (the
**no-carrier** state) → it boots the `"O R S 500 (TEST)"` diagnostic; `= 1` (carrier present) →
normal operation. SIO/0 ch.B is the **Keyboard** channel (§6.8), so the test-mode select is the
DCD line of the **keyboard connector** — a service plug/jumper there picks the mode. This is the
**only** path into the test executive — no command, keypress or fallback entry (the single
`JP Z,$031E` at reset is it).

> **As-built, the unit boots into TEST mode** *(corrected by a live logic-analyzer capture — §10.5).*
> Every channel-B `/DCD` is hardwired to GND, and a startup capture of the Cl Displ line shows the
> **`O R S 500 (TEST)`** banner — so the test executive is running, i.e. `RR0 bit 3 = 0` at reset.
> Empirically, therefore, **grounded `/DCDB` (low) → `bit 3 = 0` → TEST mode** (the SIO's RR0 DCD
> bit follows the pin level here; an *earlier draft had this polarity backwards*). Consequences:
> **no board mod is needed to reach test mode — it's the as-wired default**; to force *normal*
> operation you would instead lift the **SIO/0's pin 31 (`/DCDB`)** off GND and pull it **HIGH**.
> (Pin 31 is the channel the reset DCD test reads — the keyboard channel.) The display refresh is
> also **timer-driven** (`…→0x0403→0x0508`, §6.8/§10), so the displays are served continuously.

> **Correction to an earlier guess:** `0x013C` is **not** I/O bring-up — it is a tiny
> `DJNZ`/`DEC C` busy-wait used as a **power-on settle delay** (`0x014A` chains five of them
> for a longer pause). The real chip programming is the `OUT`/`OTIR` loop from `0x0016`.

### 3.1 Programming the chips — the init helpers

The first real work is programming the peripheral chips. Three tiny helpers do all of it:

```
02C4  LD B,$03 / LD HL,$030D / OTIR / RET     ; blast a 3-byte register table to port (C)
02CC  LD B,$08 / LD HL,$02F9 / OTIR / RET     ; blast an 8-byte register table to port (C)
02D4  ... write/readback 0xAA then 0x55 on port (C) ... ; presence/health test
```

`OTIR` streams `B` bytes from `(HL)` to **the same port `(C)`** — exactly how you
initialise a register-pointer device like a Z80 SIO. The caller loads `C` with the target
control port and calls the helper.

**The 8-byte SIO/DART init table at `0x02F9`:** `01 00 03 C1 04 47 05 EA`
Read as Z80-SIO *(write-register-pointer, value)* pairs:

| Bytes | Meaning |
|---|---|
| `01 00` | WR0←point to WR1, then **WR1 = 0x00** (interrupt config) |
| `03 C1` | WR0←point to WR3, then **WR3 = 0xC1** (Rx 8 bits/char, **Rx enable**) |
| `04 47` | WR0←point to WR4, then **WR4 = 0x47** (clock ×16, parity/stop bits) |
| `05 EA` | WR0←point to WR5, then **WR5 = 0xEA** (**Tx enable**, 8 bits, RTS+DTR asserted) |

This is a **canonical Z80 SIO / DART channel setup** — about as close to a fingerprint as
8-bit hardware gets. **Confirmed: the peripheral bus is Z80 SIO/DART async serial.**

**The presence test at `0x02D4`** writes register-pointer `2`, stores `0xAA`, reads it
back; then repeats with `0x55`. SIO/CTC channel-B register 2 (the interrupt-vector
register) is read/writeable, so this is a cheap "is the chip there and alive?" probe. The
same 0xAA/0x55 walking pattern reappears verbatim in the self-test (`0x0376`).

### 3.2 Power-on self-check (`0x00E0`)

After programming the chips, the boot path runs a **hardware + RAM self-check** before going
live (note: it does **not** checksum the ROM — that lives in service mode, §7.2):

```
00E0  CALL $01FE         ; hardware status check  → A≠0 means FAIL
00E5  JR NZ,$010E        ;   → fail: error handler (blink / halt)
00E9  CALL $015A         ; RAM test (0x55 then 0xAA over 0x8000–0xBFEF, 16 KB, via LDIR+CPIR)
00EE  JR NZ,$010E        ;   → fail: error handler
00F0  ...OUT ($43)/($01) ; final WR5 setup on the host SIO channels (Tx/RTS enable)
010B  JP $0070           ; → main: enumerate peripherals and enter the receive loop
```

* **`0x01FE`** is a **hardware status check**, not a checksum: it pokes the `0xF000` latch,
  then reads SIO@`0x30` status (`IN ($33)`, `BIT 5`) and returns pass/fail — i.e. "is the
  board/host link alive?"
* **`0x015A`/`0x016B`** is the RAM test — a `0x55` fill verified with `CPIR`, then `0xAA` —
  and is what pins RAM at **16 KB, `0x8000–0xBFFF`**.
* A failure falls to **`0x010E`**, the error handler (it re-enters via `0x011B`).

---

## 4. I/O port map

Every immediate `IN`/`OUT` in the ROM was tallied. The ports group into **devices on
16-byte boundaries**, each using offsets `+0..+3`:

A **74LS138** decodes the chip selects: inputs `A4/A5/A6`, enabled by `A7`-low + `/IORQ`-low +
`/M1`-high (the `/M1` gate suppresses false I/O selects during interrupt-acknowledge). Its
outputs (confirmed from the board schematic) are:

```
'138 out   Device base  Ports          Chip select   Identity
--------   -----------  -------------  -----------   --------------------------------
O0 (15)    0x00         00 01 02 03    DART1_CE      Z80 DART  (data A/B = +0/+2, ctrl = +1/+3)
O1 (14)    0x10         10 11 12 13    DART2_CE      Z80 DART
O2 (13)    0x20         20 21 22 23    EXP_13        EXPANSION SOCKET (unpopulated) — tested on 0x23
O3 (12)    0x30         30 31 32 33    DART3_CE      Z80 DART   (0x33 = busiest input port)
O4 (11)    0x40         40 41 42 43    SIO_CE        Z80 SIO/0  ← Cl Displ (ch.A) + Keyboard (ch.B, DCD test §3)
O5 (10)    0x50         50 51 52 53    CTC1_CE       Z80 CTC    (baud gen; IM-2 vec base 0x50 — §5/§6.7)
O6 (9)     0x60         60 61 62 (63)  CTC2_CE       Z80 CTC    (IM-2 vec base 0x58 — §5)
O7 (7)     0x70         —              (unused)      —
```

**The `SIO_CE` chip is the `0x40–0x4F` device** — its channel A (`TxDA`, pin 15) drives the
**`Cl Displ` customer display** (via a 74LS06, §6.9) and its channel B (port `0x42`) is the
**Keyboard** input (§6.8). The **host link is *not* here — it is DART1 ch.B (port `0x02`)** (§6).

Reasoning (now matched against the hardware — **one Z80 SIO/0 + three Z80 DART + two CTCs**):

* The `+0/+1/+2/+3` layout with **odd ports as control** and a register-pointer protocol
  is the Z80 SIO/DART signature (each chip = 2 async channels).
* The board carries **one Z80 SIO/0 (`Z0844004PSC`) and three Z80 DARTs**. The DART is
  async-only; the SIO/0 is the only sync-capable part — and the boot init programs **every**
  channel async (WR4 = `0x47`, ÷16), so **this firmware never uses synchronous mode.** Curiously
  the SIO/0 sits on the **display/keyboard** channels (`SIO_CE` = `0x40–0x4F`), not the host
  link, so its sync capability goes unused — likely a BOM/availability choice (or the keyboard
  console was once meant to run clocked). The three
  DARTs carry the host link, the keyboard's host-facing side, and the other serial peripherals.
  *(The SIO/0's ch.B `/DCDB` is grounded like every other DCD on the board, tying off the
  reset-time test selector — §3.)*
* The firmware addresses **five** SIO bases (`0x00–0x40`) but only **four chips are fitted**.
  The fifth — base **`0x20`** — is the **unpopulated expansion connector**: the boot presence
  test pokes port `0x23` and records the result in `0x9A91` (§6.3), and when the chip is
  absent the dispatcher switches to **Map B**, which disables exactly **peripherals 3 and 4**
  (the two channels that live on that socket). So the firmware was written for an
  expansion-ready board and degrades gracefully when the extra SIO isn't installed.
* Four fitted SIOs = **8 serial channels**; with the expansion SIO that becomes 10. The
  dispatcher (§6.3) drives up to **9 templated peripheral channels** plus the host/comms
  channel on `0x40` — i.e. a fully-loaded board uses essentially the whole bank, with `MPX`
  (peripheral 0) being the controller answering for itself.
* `0x40` is the **host/primary** channel: port `0x43` is the most-written control port
  (42 writes) and it carries the TEST-mode DCD jumper.
* Devices `0x50`/`0x60` get a **3-byte** init (not the 8-byte SIO sequence) and are **Z80
  CTCs**: cmd 1 of every channel writes a baud time-constant to a `0x5x` port (§6.7), and the
  IM-2 setup hands them interrupt-vector bases `0x50`/`0x58` (§5). They generate the serial
  bit clocks and the periodic tick. *(Which CTC channel clocks which SIO channel still wants a
  board trace; the chip identity is confirmed from the programming sequences.)*

> Blog-ready one-liner: *"The MX-08 is essentially a Z80 wrapped in a wall of Zilog serial
> chips — a 1992 USB hub for cash-register peripherals."*

---

## 5. Interrupt model

```
0AFD  IM 2          ; mode-2 vectored interrupts
0B01  LD I,A        ; load the interrupt-vector page register
0D05  LD I,A        ; (set up again on a second path)
0D07  IM 2
```

**Confirmed: Z80 interrupt mode 2.** Each SIO/CTC channel supplies a vector on the
daisy chain; `I` holds the high byte of the vector table, the peripheral supplies the low
byte, and the CPU jumps through a table of ISR addresses. This is the standard way to run
a multi-SIO Z80 board and is exactly what you'd expect for a device whose whole job is
servicing many serial streams concurrently.

The setup at `0x0AFD` makes this concrete:

```
0AFF  LD A,$97 / LD I,A      ; vector table lives in RAM page 0x97xx
0B0D  LD A,$50 / OUT ($50),A ; CTC@0x50 interrupt vector base = 0x50
0B13  LD A,$58 / OUT ($60),A ; CTC@0x60 interrupt vector base = 0x58
0B15  LD HL,$0CD4 / LD ($9750),HL   ; install ISR pointers into the table ...
0B1B  LD HL,$0CDA / LD ($9752),HL   ; ... 0x9750, 0x9752, 0x9754, 0x9756 → 0CD4/0CDA/0CE0/0CE6
```

So the **IM-2 vector table sits at `0x9750`** (page `0x97` + the `0x50`/`0x58` vector bases
the CTCs were given), and the per-channel ISRs live at `0x0CD4` onward. The CTCs (clocked at
**4 MHz**, half the 8 MHz main crystal) both generate the periodic interrupts *and* supply
the daisy-chain vectors. Note the table is in **RAM**, so the ISR pointers are installed at
boot rather than baked in ROM.

> **System clock.** The 8 MHz crystal is **divided by 2** to a **4 MHz** Φ that clocks the Z80
> *and* every peripheral — the SIO/0 (`Z0844004PSC`) and CTCs are 4 MHz-rated parts and share
> the CPU bus clock, so the whole machine runs at 4 MHz. (All baud/tick math here uses 4 MHz.)

The eight CTC-channel ISRs are deliberately tiny — each just bumps a counter in the Z80's
*alternate* register set and returns:

```
0CD4  EXX / INC B / EXX / EI / RETI     ; tick counter for one CTC channel
0CDA  EXX / INC C / EXX / EI / RETI     ; ... INC C, D, E, H, L for the rest,
0CF8  INC IX / EI / RETI                ; ... and INC IX / INC IY for the last two
```

Using `EXX` means an interrupt costs no stack traffic for register saves — just swap banks,
increment, swap back. The foreground code reads these shadow counters to measure time.

### 5.1 Software serial FIFOs

Transmit is buffered. The enqueue routine at `0x5F6E`:

```
5F6E  DI
5F6F  LD HL,($99EA)     ; HL = ring write pointer
5F72  LD (HL),B         ; store byte 1 (e.g. channel/cmd)
5F73  INC HL
5F74  LD (HL),C         ; store byte 2 (payload)
5F76  RES 3,H           ; wrap: clear bit 3 of high byte → buffer confined to a window
5F78  LD ($99EA),HL     ; save write pointer
5F7B  LD DE,($99EC)     ; DE = read pointer
5F81  SBC HL,DE         ; full? (write caught up to read)
5F83  JP Z,$657F        ; overflow handler → DI/HALT (wait for ISR to drain)
```

* Write pointer at **`0x99EA`**, read pointer at **`0x99EC`**.
* `RES 3,H` is the wraparound trick: it forces the pointer to stay inside an
  **8-page-aligned circular buffer** without a compare-and-reload.
* The matching consumer side (`0x5F87`: `IN A,($00)`, mask, store) is the **ISR** that
  pulls bytes out and pushes them to the SIO data port. Classic producer (main loop) /
  consumer (interrupt) FIFO.

### 5.2 The 13.3 ms system tick

One CTC channel is set up as the master time-base. **CTC0 channel 3** (port `0x53`) is
programmed control-word `0xB5` → **timer mode, prescaler ÷256, interrupt enabled** — with
time constant `0xD0` (208):

> **tick = (256 × 208) / 4 MHz = 13.31 ms** (≈ 75 Hz)

This tick paces a chain of software timers (`0x999E … 0x99FA` and friends) that decrement the
per-channel receive timeouts and the cash-drawer strobe countdown (§6.6). It's the unit's
fundamental scheduling quantum — every software-timed event is a multiple of 13.3 ms.

---

## 6. The host protocol and command dispatcher

The heart of the firmware (`~0x4164–0x5F00`) is a **byte-at-a-time state machine** that
parses a command stream from the host, plus a **two-level table dispatcher** that routes
each decoded command to a per-peripheral handler. This is the part that does the real work,
and it is now fully mapped.

**Where the host connects — the host link is DART1 channel B (port `0x02`).** *(Earlier drafts
wrongly put the host on SIO/0 ch.B; board tracing corrected it — see below.)* This is the unit's
control uplink: the cash register's brain (a **PC**, or a **video console** acting as master)
drives everything from here. The link is **bidirectional**: host command frames arrive on DART1
ch.B `RxDB` (the receive ISR at `0x5E17` reads `IN ($02)` and, for frame bytes, fills the parser
ring `0x99EE`→`0x99F0`), and the MX-08 returns replies/data wrapped back into the same
nibble-tagged format (encoder at `0x10D5`/`0x1110`). Because the MX-08 carries no application
logic of its own (§6.8), **nothing happens until a host drives this link** — it is a pure I/O
concentrator.

**This is the actual "MPX" (multiplexer) function.** The MX-08 collects bytes from *all* the
other peripheral serial inputs into **one shared "to-host" ring (`0x99EA`)** — the receive ISRs
for ports `0x00`, `0x10`, `0x12`, `0x20`, `0x22` each write into it, and peripheral-handler
replies are enqueued there too (via `0x5F6E`) — then streams that up to the host. Inbound, it
demultiplexes host commands out to the displays / printer / drawer. The **keyboard is a special
case**: it lands on **SIO/0 ch.B (port `0x42`)**, and its keystrokes are buffered separately at
`0x9A5C` for the host to poll. The **reset-time test-mode `/DCD` (port `0x43`) therefore sits on
the *keyboard* channel** (SIO/0 ch.B) — a service jumper on the keyboard connector selects test
mode (§3).

### 6.1 The receive loop and parser state machine (`0x4164`)

`0x4164` is **not** a "default stub" (an earlier guess) — it is the **main receive loop**.
Every handler ends with `JP $4164` meaning *"frame done, fetch the next byte."*

```
4164  LD HL,($99F0)     ; HL = RX ring read pointer
4167  LD A,($99EE)      ; A  = RX ring write pointer (low byte)
416A  CP L              ; buffer empty?
416B  JP Z,$5968        ;   → yes: run background/idle tasks
416E  LD C,(HL)         ; C = next received byte
416F  INC L / LD ($99F0),HL   ; advance read pointer
4173  LD A,C
4174  LD HL,($9A00)     ; HL = CURRENT PARSER STATE handler
4177  JP (HL)           ; dispatch to it
```

So **`0x9A00` holds a function pointer to the current parser state.** Each state consumes
one byte, decides the next state (by writing `0x9A00`), and jumps back to `0x4164`. The RX
side is the mirror of the TX FIFO from §5.1: **read ptr `0x99F0`, write ptr `0x99EE`** (the
SIO receive ISR fills it).

When the RX buffer is empty the loop jumps to `0x5968`, which runs a **second, independent
state machine** through a function pointer at **`0x9A02`** — the background/housekeeping task
(host-channel status polling on port `0x43`, timeout counters at `0x98F3`, etc.). So the
firmware cooperatively multitasks two state machines off the one main loop: `0x9A00` drives
the *command parse*, `0x9A02` drives *background work*, and neither ever blocks.

### 6.2 The wire protocol — a nibble-encoded command frame

Walking the states (`0x4178 → 0x418C → 0x41B5 → …`) decodes the on-the-wire framing. The
host addresses a peripheral and issues a command using **tagged nibbles** in the high bits:

| Byte pattern | State action | Meaning |
|---|---|---|
| `1100 xxxx` (`≥0xC0`) | `(byte & 0x1E) >> 1` → `0x9A46` | **Frame start** + peripheral select (0–15) |
| `1000 nnnn` (`0x8n`) | low nibble must equal `0x9A46` | **Peripheral confirm** (echo/guard byte) |
| `0100 cccc` (`0x4n`) | `byte & 0x0F` → `0x9A47`, must be `<8` | **Command code** (0–7) |
| data byte | → `0x9A48` (cmd 1) or `0x9978` (cmd 7) | **Command parameter** |

So a typical command frame is roughly `0xC? 0x8? 0x4c [param…]`: *select peripheral → confirm
→ command → optional argument*. Two parser variables fall out of this:

* **`0x9A46`** = selected **peripheral index** (0–15)
* **`0x9A47`** = **command code** (0–7)
* **`0x9A48`** = command **parameter** (e.g. baud-rate index for the "open" command)

Anything that doesn't match resyncs to the frame-start state (`0x41A3 → 0x4178`), so a
garbled byte can't wedge the parser — it just waits for the next `0xC?` lead-in.

### 6.3 The two-level dispatch tables

Once a full `peripheral + command` is decoded, control falls into the dispatcher at
`0x42C4`:

```
42C8  LD A,($9A46) / RLCA      ; peripheral index × 2
42CF  LD BC,$4334              ; MASTER TABLE A (primary)
42D2  CALL $119A / JR NZ ...   ; a mode flag picks ...
42D7  LD BC,$4354              ; ... MASTER TABLE B (alternate)
42DA  ADD HL,BC / LD E,(HL)/LD D,(HL)   ; DE = per-peripheral SUB-TABLE
42DE  LD A,($9A47) / RLCA      ; command × 2
42E5  ADD HL,DE / LD …         ; HL = handler address
42E9  EX DE,HL / JP (HL)       ; go
```

* **Master table A** (`0x4334`, 16 entries) and **B** (`0x4354`) map *peripheral → sub-table*.
  They are identical except peripherals **3 and 4 are disabled in table B** (point at the
  empty default sub-table). The selector at `0x119A` just reads RAM flag **`0x9A91`**, which
  the boot presence test sets according to whether the **optional SIO at port base `0x20` is
  fitted** (`0x0376` does the `0xAA/0x55` readback on port `0x23` and writes `0x9A91`). On the
  physical board that socket is the **unpopulated expansion connector** (§4), so
  **Map B = "expansion SIO absent" → peripherals 3 and 4 disappear.** This is the
  hardware-options switch — and it confirms the expansion connector was meant to carry a
  fifth SIO adding two more peripheral channels.
* Each **sub-table** has 6 command slots; slot 0 is unused (`0x4164`), slots 1–5 are real.

Decoded master-A map (peripheral → sub-table → handlers):

| Periph | Sub-table | Commands 1…5 (handler entry points) | Notes |
|---|---|---|---|
| 0  | `4384` | `44C8` only | **MPX** — identity/version (see §6.5) |
| 1  | `4394` | `4606 45C0 4560 4565 451D` | templated serial channel |
| 2  | `43A4` | `483D 47F7 4797 479C 4754` | templated serial channel (decoded below) |
| 3  | `43B4` | `4A74 4A2E 49CE 49D3 498B` | templated *(off in map B)* |
| 4  | `43C4` | `4CAB 4C65 4C05 4C0A 4BC2` | templated *(off in map B)* |
| 5  | `43D4` | `4EE2 4E9C 4E3C 4E41 4DF9` | templated serial channel |
| 6  | `43E4` | `5119 50D3 5073 5078 5030` | templated serial channel |
| 7  | `43F4` | `5352 530C 52AC 52B1 5269` | templated serial channel |
| 9  | `4434` | `444C 4485 448F` | **strobe outputs** (see §6.6) |
| 10 | `4404` | `5598 5545 54E4 54E9 54A0` | templated serial channel |
| 11 | `4414` | `57F3 57C8 5779 577E 5747` | templated serial channel |
| 15 | `4424` | `58E9` only | single status handler |

The nine templated channels (1,2,3,4,5,6,7,10,11) map onto the named endpoints
`KEYB OPDS CLDS FISC C` plus spares; **MPX is peripheral 0**, the controller answering for
itself. *(The exact index→name binding is set at runtime when channels are enumerated; the
ordering above is confirmed, the name labels are inferred.)*

### 6.4 The templated channel command set (5 identical commands)

Every serial channel block is **the same code stamped out per channel** — the five handlers
sit at identical relative offsets (`+0x15, +0x58, +0x5D, +0xB8, +0xFE`) in each block, and
differ only in the RAM addresses and bus-address byte they bake in. Each channel owns a
**32-byte state block** in RAM (channel 2 at `0x9821`, channel 3 at `0x9841`, … stride
`0x20`), and a **bus address byte** (`0x82`, `0x83`, …). Decoding channel 2 (the `0x43A4`
block) gives the vocabulary for all of them:

| Cmd | Handler (ch.2) | What it does |
|---|---|---|
| **1** | `483D` | **OPEN / RESET channel.** `OUT ($11),$18` (Z80-SIO channel-reset), zero the 26-byte state block, then program the **CTC** (`OUT ($52),…`) with a **baud time-constant** fetched from table `0x432D` indexed by the parameter byte `0x9A48`. *This is what proves `0x50/0x60` are Z80 CTCs generating the baud clocks.* |
| **2** | `47F7` | **SET LINE PARAMETERS.** `OTIR` of an 8-byte SIO register block (from `0x4444`) to port `0x11` — reprograms the channel's framing (bits/parity/stop), then arms a receive timeout. |
| **3** | `4797` | **ENABLE.** `SET 3,(state+1)` then transmit a status frame back to the host. |
| **4** | `479C` | **CONFIGURE / MODE.** Branches on a mode byte (`0x9824`), updates the control byte, loads a vector, then transmits a status frame. |
| **5** | `4754` | **POLL / STATUS.** Transmit a 4-byte status response: `header, state, computed-status, state2`. This is the routine the host hits repeatedly to pump data to/from the device. |

The transmit half of every handler is a sequence of `CALL $5F6E` (the FIFO-enqueue from
§5.1), so each "response" is just bytes pushed into the per-channel TX ring and clocked out
under interrupt. The response always leads with a **`0xC?` + bus-address** pair — the same
framing the host uses, so the link is symmetric.

### 6.5 Worked example — MPX "report identity" (`0x44C8`)

Peripheral 0's single command answers the host's "who are you?":

```
44C8  LD B,$C1 / LD C,$80 / CALL $5F6E     ; queue frame header (0xC1, 0x80)
44CF  LD HL,$9A99 / LD E,$32               ; 0x32 = 50 bytes from RAM 0x9A99 ...
44D4  loop: LD C,(HL) / CALL $5F6E / INC HL / DEC E / JP NZ  ; ... queued out
44E1  LD HL,$44FB / LD E,$0D               ; 13 bytes from 0x44FB ...
44E6  loop: LD C,(HL) / CALL $5F6E / ...   ; ... = the string "I 2.1 17nov92"
44F3  LD C,$04 / CALL $5F6E                ; queue trailer 0x04
44F8  JP $4164                             ; done → receive loop
```

A 50-byte live-status block (its field layout is reversed in §7.4 — it doubles as the
service display) followed by the **ASCII firmware version `I 2.1 17nov92`** and a trailer.
One routine that ties together the parser, the dispatch tables, the version string, and the
serial FIFO — a great figure for the blog.

### 6.6 The odd one out — memory-mapped strobe outputs (`0x444C`)

Peripheral 9 is not a serial channel. Its command 1 handler writes directly to two
**memory-mapped output latches**:

```
444C  LD A,($9A48) / CP $04 / JP NC,$4164   ; param must be 0..3
...   BIT 0,A → LD ($E000),A                 ; latch at 0xE000  = DRAWER A
...   BIT 1,A → CALL $119A / JP Z → LD ($F000),A  ; latch at 0xF000 = DRAWER B (expansion-gated)
447C  LD HL,$0014 / LD ($9A05),HL            ; load a ~20-tick release timer
```

These are the two **cash-drawer kick** strobes — the connector labels confirm it:
**`0xE000` = Drawer A** (always available) and **`0xF000` = Drawer B** (only fired when the
expansion is fitted — its write is guarded by the same presence check as the expansion serial
channels, `CALL $119A`; §6.8). Each is pulsed on, then released after the timed countdown at
`0x9A05`. So the address space has **two write-only hardware latches at `0xE000`/`0xF000`**,
outside the Z80 I/O map, decoded straight off the address bus (§2).

**Pulse width.** The handler loads `0x9A05 = 0x0014` = **20 system ticks** and sets a
busy-lock (`0x9A04` bit 7) so a second kick is ignored until this one finishes. The
software-timer chain decrements it one count per **13.3 ms** tick (§5.2), so the strobe is:

> **20 × 13.31 ms ≈ 266 ms** — about a quarter-second, a textbook cash-drawer kick.

> #### Seeing the strobe on the bench (no drawer required)
>
> The strobe is a firmware-driven TTL pulse — the solenoid is only the load, so you can watch
> it on the bare PoS unit.
>
> * **Where to probe:** the **output of the latch IC** fed by the second LS139's `Y2` (`0xE000`)
>   or `Y3` (`0xF000`) — a clean 0/5 V edge regardless of load. The drawer-connector kick pin
>   works too, but its driver is usually open-collector/Darlington into a ~24 V solenoid line,
>   so with no drawer that node may float — add a few-kΩ pull-up to the drawer supply for a
>   clean swing.
> * **Expected shape:** a single ~**266 ms** active pulse (20 × 13.3 ms), one-shot per command.
> * **How to fire it — host command** (peripheral 9, command 1) on the host SIO channel
>   (base `0x40`), 4 bytes: `D2 89 41 01` → strobe `0xE000`; `…02` → `0xF000`; `…03` → both.
>   Try the host link at 9600 first (§6.7). The byte meanings: `D2` = select peripheral 9,
>   `89` = confirm 9, `41` = command 1, `01/02/03` = which output(s).
> * **How to fire it — at power-on:** the boot hardware-status check writes both latches
>   (`0x01E8` → `0xE000`, `0x01FE` → `0xF000`), so a storage scope triggered on power-up will
>   catch latch activity without any host at all.

### 6.7 Line settings — baud rates and framing

The "OPEN" command (cmd 1) programs the CTC baud generator from a small table indexed by the
parameter byte. The **baud table at `0x432D`** is:

```
index:  0    1    2    3    4    5    6
TC:    D0   68   34   1A   0D   1A   0D     (208 104  52  26  13  26  13)
```

With the board now confirmed (**8 MHz main crystal, CTCs clocked at 4 MHz**) these resolve to
exact rates. The CTC channel runs in **counter mode** — its control word `0x55` has D6=1, so
it divides its 4 MHz input directly by the time-constant rather than going through the
prescaler — and the SIO then divides by 16 (WR4=`0x47`, ×16). So:

> **baud = 4 000 000 / (16 × TC)**

| index | TC (dec) | computed baud | standard rate | error |
|---|---|---|---|---|
| 0 | 208 | 1201.9 | **1200** | +0.16 % |
| 1 | 104 | 2403.8 | **2400** | +0.16 % |
| 2 |  52 | 4807.7 | **4800** | +0.16 % |
| 3 |  26 | 9615.4 | **9600** | +0.16 % |
| 4 |  13 | 19230.8 | **19200** | +0.16 % |
| 5 |  26 | 9615.4 | **9600** | (alias) |
| 6 |  13 | 19230.8 | **19200** | (alias) |

A clean `1200 / 2400 / 4800 / 9600 / 19200` ladder, all within 0.2 % — comfortably inside
async tolerance. (A channel reprogrammed by cmd 2 to WR4=`0xC4`'s ×64 mode would run at a
quarter of these, i.e. `300 … 4800`.)

Framing comes from the SIO write-register blocks:

* **Reset/default init** (`0x02F9`): `WR4 = 0x47` → **×16 clock**, with the parity/stop bits
  for the standard async start-up.
* **"SET LINE PARAMETERS" (cmd 2)** loads block `0x4444` = `00 18 01 04 04 C4 05 00`, i.e.
  channel-reset, `WR1 = 0x04`, **`WR4 = 0xC4`** (×64 clock, 1 stop bit, no parity), `WR5 = 0x00`
  (Tx held off until enabled). So the host can re-flavour a channel's framing on the fly.

### 6.8 Channel → SIO port → physical connector map

Scanning each channel's OPEN handler (cmd 1) for the SIO control port it resets pins the
peripheral-index → hardware-channel binding. Cross-referencing the **physical connector
labels** on the unit (8 fitted + 3 blanked) closes the loop completely. *(The SIO-port column
is solid — it's each channel's reset target. The CTC-baud-channel column is read from the same
handlers and is more approximate: there are only seven CTC baud channels for ten serial
channels, so some are shared.)*

| Periph | SIO chan (ctrl port) | CTC ch (baud) | Physical connector | Fitted? |
|---|---|---|---|---|
> **Note — corrected by board tracing + RX-ISR analysis.** The *output* assignments below are
> confirmed (displays, drawers). The host and keyboard were originally mis-assigned to SIO/0 ch.B;
> the receive ISRs settle it: **port `0x02` (DART1 ch.B) feeds the command parser → that is the
> HOST**; **port `0x42` (SIO/0 ch.B) feeds the keystroke buffer → that is the KEYBOARD**. The
> remaining DART1/DART2 *input* channels (`0x00`/`0x10`/`0x12`) all feed the shared "to-host" ring
> (`0x99EA`), so the precise Printer/Aut-Inp/Serial 1 split still wants the RX-ISR cross-check.

| Channel (data port) | Role | Connector | Evidence |
|---|---|---|---|
| SIO/0 ch.A (`0x40`) | display **out** | **Cl Displ** — pin 15 → 74LS06 gate 2 (3→4) | trace ✓ |
| SIO/0 ch.B (`0x42`) | input → buffer `0x9A5C` | **Keyboard** (74LS14 cleanup) | trace + `IN ($42)` ✓ |
| DART1 ch.B (`0x02`) | **host commands in** → parser | **host link** (likely `PC/VID`) | `IN ($02)` → `0x99EE` ✓ |
| DART1 ch.A (`0x00`) | input → to-host ring | Printer / Aut-Inp / Serial 1 | `IN ($00)` → `0x99EA` |
| DART2 ch.A (`0x10`) | input → to-host ring | Printer / Aut-Inp / Serial 1 | `IN ($10)` → `0x99EA` |
| DART2 ch.B (`0x12`) | input → to-host ring | Printer / Aut-Inp / Serial 1 | `IN ($12)` → `0x99EA` |
| DART3 ch.A (`0x30`) | display **out** | **Op. Displ** — pin 15 → 74LS06 gate 5 (11→10) | trace ✓ |
| DART3 ch.B (`0x32`) | **clock gen** for ch.A | *(no connector)* — pin 27 → ch.A `/TxCA` pin 14 | trace ✓ |
| EXP ch.A/B (`0x20`/`0x22`) | input → to-host ring | **Serial 2 / Serial 3** | expansion socket ✓ |
| `0xE000` / `0xF000` latch | strobe **out** | **Drawer A / Drawer B** | §6.6 ✓ |

**What's now pinned vs. inferred.** Board traces confirm both character displays hang off the
**same 74LS06** open-collector inverter, on different gates:
**`Cl Displ` = SIO/0 ch.A** (port `0x40`), pin 15 (`TxDA`) → 74LS06 gate 2 (pin 3→4) → connector;
**`Op. Displ` = DART3 ch.A** (port `0x30`), pin 15 (`TxDA`) → 74LS06 gate 5 (pin 11→10) → connector.
The firmware also writes **DART3 ch.B** (port `0x32`), but a board trace shows ch.B is **not a
display** — its `TxDB` (pin 27) is wired straight to ch.A's `/TxCA` (pin 14), so **ch.B
generates the transmit clock for the OP DISPL channel.** (With only seven CTC baud channels for
ten serial lines, §6.7, repurposing a DART channel as a clock source is the economising trick.)
So there are exactly two character displays — Op. Displ (DART3 ch.A) and Cl Displ (SIO/0 ch.A) —
and ch.B is internal clock plumbing, not a connector. That leaves the four DART1/DART2 channels
(periph 1, 2, 5, 11) as **Keyboard, Printer (fiscal),
Aut-Inp, and Serial 1** — *as a set* — with the individual one-to-one not separable from the
firmware (see the key insight below).

**Key insight — the MPX is device-agnostic.** All nine serial channels run the *identical*
five-command template (§6.4); there is **no keyboard-decode, display-render or printer logic
in the channel handlers**. The MPX is a pure pipe — the host decides what each channel *means*
and drives it accordingly. The device names exist only as **cosmetic labels in the service
self-test**: the display builder at `0x04A4` prints `MPX` as a fixed header, then emits
`KEYB`/`OPDS`/`CLDS`/`FISC` (from string table `0x067E`/`0x0683`/`0x0688`/`0x068D`) under the
pass/fail markers in status slots `0x9AE3[0..3]`. So beyond the display channels, "which port
is the keyboard" isn't a firmware fact at all — it's a wiring/host convention printed on the
connector panel. (The boot self-test does walk every channel in a fixed order, `B`=1…11 at
`0x0073`, but that's a test sequence, not a name map.)

> **The keystone confirmation.** The unit's **three blanked connectors — Serial 2, Serial 3,
> Drawer B — are exactly the three expansion-gated features in the firmware.** Serial 2/3 are
> peripherals 3 & 4 on the **expansion SIO at base `0x20`** (disabled by Map B when the chip is
> absent, §6.3); Drawer B is the `0xF000` strobe, whose handler calls the *same* presence flag
> (`CALL $119A; JP Z` at `0x446E`) before firing. Firmware and bare PCB agree perfectly: the
> board was designed for one more SIO (two serial + nothing) and the firmware degrades to the
> 8-port configuration when it isn't fitted. The SIO/0's two halves serve **`Cl Displ`** (ch.A,
> port `0x40`, via a 74LS06 — §6.9) and the **Keyboard** (ch.B, port `0x42`, via a 74LS14; its
> `/DCDB` is the reset DCD test, §3). The host link is elsewhere — DART1 ch.B, port `0x02` (§6).

### 6.9 The "video" output — there isn't any

The unit drives a customer **display** (the `Cl Displ` connector — a serial-fed monitor/VFD,
which is the "video"-style screen one might expect), but the MX-08 contains **no
video-generation hardware whatsoever**. The complete I/O space the firmware touches is:

```
0x00–0x43  →  the SIO/0 + three DARTs (+ the expansion SIO)
0x50–0x62  →  two Z80 CTCs
0xE000/0xF000 → the two drawer latches
```

There is **no CRTC, no video RAM, no character-generator ROM, no dot-clock or sync** — none
of the machinery a raster signal needs. So the board cannot and does not emit composite/RGB
video.

**The customer display is simply SIO/0 channel A — an ordinary async serial port**, identical
in operation to the keyboard/printer/other-display channels. The transmit path is textbook
polled UART:

```
055E  IN A,($41) / AND $04    ; wait for SIO RR0 bit 2 = Tx buffer empty
0569  OUT ($40),A             ; write one ASCII byte to the SIO data port
056B  INC HL / loop until 0xFF
```

and its OPEN handler (`0x5119`) programs a CTC baud rate from the same `1200…19200` table
(`0x432D`) with the same async framing (WR4 = `0x47` ÷16) as every other channel.

So the picture is drawn **by the external device**: `Cl Displ` feeds a *serial* display — a
customer monitor/VFD that carries its **own** video electronics — and the MX-08 just streams it
text. The operator display on DART3 works the same way.

**The line driver is one shared `74LS06`** (hex inverter, *open-collector*), not an RS-232
transceiver. Both character displays use different gates of it:

* **Cl Displ** — SIO/0 pin 15 (`TxDA`) → 74LS06 **gate 2** (pin 3 → pin 4) → connector
* **Op. Displ** — DART3 pin 15 (`TxDA`) → 74LS06 **gate 5** (pin 11 → pin 10) → connector

So each display link is **inverted, open-collector short-haul serial** — *not* ±12 V RS-232 —
and the idle/active levels depend on the output pull-up rail (+5 V → inverted TTL; a higher
rail → current-loop-ish). On a scope: at the **SIO/DART TxD pin** it's clean async serial,
1 start / 8 data / no parity / 1 stop, idle-high, host-selected baud; at the **74LS06 output**
it's that signal *inverted*. It is, in short, **a POS terminal with a "video"-style display and no video chip**:
the video lives in the monitor; the box only speaks serial to it — the
device-agnostic-multiplexer theme (§6.8) taken to its logical end.

---

## 7. Self-test mode (`0x031E → 0x033C`)

Entered when the TEST jumper pulls DCD low at reset. It:

1. Re-points the stack (`LD SP,$0692`, then `LD SP,$9700`).
2. Clears a block of RAM (`LD (HL),$3F` + `LDIR` over `0x9A99…`; `0x3F` = ASCII `?`, the
   "untested" placeholder you see in the `ROM ?` / `RAM ?` prompts).
3. Runs the **same 0xAA/0x55 readback** against the SIO chips (`0x0376`).
4. Calls a chain of test subroutines (`0x0CC1`, `0x099C`, `0x08E3`, `0x0A27`, …); `0x099C`
   first resets every SIO channel (`OUT $18` to each control port), then the tests fill a
   display buffer at `0x9AB7` and exercise each named peripheral
   (`MPX/KEYB/OPDS/CLDS/FISC/C`) over its serial channel.

The human-readable labels (`ROM ?`, `RAM ?`) confirm there was a **service display** (one of
OPDS/CLDS) the technician watched during diagnostics.

### 7.1 The serial loopback test — and why "SALSI ADRIANO" is in the ROM

The per-channel link test (`0x0214`, called as `0x0193`/`0x0199`+ with a port base in `C`)
is a **transmit-and-read-back loopback**: it walks the string at `0x0310`, sends each byte
out the SIO data port, waits on the status bit, reads the echo, and compares
(`IN (C)` vs the sent byte) until it hits the `0x04` terminator.

That string is **`"SALSI ADRIANO"` `04`** — so the programmer's name isn't only a credit, it
is the **loopback test pattern** the firmware clocks through every serial channel during
diagnostics. (The self-test sweeps port bases `0x00`, `0x10`, `0x22`, … so each SIO channel
is tested in turn.)

### 7.2 The ROM checksum (`0x09B9`) — present in code, **blank in this dump**

The `ROM ?` test computes a 16-bit checksum over `0x0000–0x7FFC`:

```
09BA  LD HL,$0000 / LD BC,$7FFD / LD DE,$0000
09C3  loop: A = L          ; low byte of current address
09C4        XOR (HL)       ;   XOR the ROM byte (position-weighted)
09C5        ADD A,E        ;   add into running 16-bit sum (DE)
      ...    INC HL / DEC BC / loop
09DB  LD BC,($7FFE)        ; load the STORED checksum from the top 2 ROM bytes
09DF  EX DE,HL             ; HL = computed, BC = stored → caller displays/compares
```

So the firmware reserves the **top two bytes of the EPROM (`0x7FFE-0x7FFF`) for a stored
checksum**. In *this* dump those bytes are `FF FF` (blank), and the computed value is
`0x35FC` — they don't match. Together with the entire `0x6581-0x7FFF` tail being blank
`0xFF`, this strongly suggests the image is a **development / un-finalised burn**: the
checksum slot was never programmed by the EPROM tooling. Worth a line in the blog — the dump
is functionally complete but was never "sealed."

**So why does the unit still pass self-test and run?** Because the ROM checksum is **never a
boot gate**. The grep is decisive: the normal boot path (`0x0000–0x0110`) makes *zero* calls
into `0x09B9`. The only self-test that can actually halt the machine (the `0x010E` error
handler) checks **hardware status** (`0x01FE`) and **RAM** (`0x015A`) — never the ROM. The
checksum routine is reachable **only in service mode**, as one entry in the test jump-table
(`0x0324: JP $09B9`) behind the TEST jumper, and even then it just **computes the value and
returns it for the `ROM ?` display** — it never branches to a failure/halt. A blank or wrong
checksum is therefore invisible during everyday operation; at worst a technician running the
service menu would see an odd `ROM ?` readout while the register keeps working.

### 7.3 The test executive and its jump-table of diagnostics (`0x031E`)

When the DCD check at reset (§3) selects test mode (`JP Z,$031E`), control lands on a
**10-entry jump table** at `0x031E`. Each `JP` is one diagnostic; the executive at `0x033C`
runs them and paints results into the display buffer at `0x9A9B–0x9AC9` (the same RAM the MPX
"identity" reply ships out — it *is* the status/display area). Decoded:

| Slot | Target | Diagnostic (purpose) |
|---|---|---|
| `0x031E` | `0x033C` | **Test executive** — sequences the tests, drives the `O R S 500 (TEST)` display |
| `0x0321` | `0x069A` | **CPU register / ALU test** — loads every register, `CPL`/`ADD`/flag checks, fails to `0x0717` |
| `0x0324` | `0x09B9` | **ROM checksum** (§7.2) |
| `0x0327` | `0x071C` | **RAM pattern test** — walks a descending pattern from `0x8000` across the 16 KB |
| `0x032A` | `0x0AF0` | **Live run under IM-2** — installs the interrupt vectors/CTCs and enters interrupt-driven operation |
| `0x032D` | `0x092D` | **Peripheral/display test** — resets the SIOs, writes a 5-char `?` field at `0x9A9B` |
| `0x0330` | `0x089D` | **Peripheral/display test** — 3-char field at `0x9AA7` |
| `0x0333` | `0x09E5` | **ROM-test display variant** — 16-char field at `0x9AB7` |
| `0x0336` | `0x076F` | **Peripheral test** — pattern `'K'` (`0x4B`) at `0x9AC7` |
| `0x0339` | `0x0A28` | **Display test** — pattern `'='` (`0x3D`) at `0x9AC9` |

Every diagnostic shares the same prologue — `CALL $0CC1 / $099C / $08E3 / $0A27` — which
**resets all SIO channels and re-inits the display** before the specific test runs. The fill
characters tie back to the strings table: `0x3F` = `?` ("untested"), `0x3D` = `=` (field
separator/pass marker), matching the `"ROM,RAM ?"` and `"0?=?="` templates at `0x05B7`/`0x0639`.

The two genuinely useful entries for a service tech are the **register test** (`0x069A`) and
the **RAM pattern test** (`0x071C`); the rest drive the display and the per-channel
loopback (§7.1). Slot `0x032A` is the odd one — it's effectively "**boot normally, but from
the test path**," bringing the box up under interrupts.

### 7.4 The 50-byte status / display block (`0x9A99–0x9ACA`)

This is the buffer the MPX "identity" command ships to the host (§6.5) — and it is the very
same RAM the test executive paints. In other words, **the host-readable status block and the
service display are one and the same image.** Boot/entry fills the whole thing with `0x3F`
(`?` = "untested") via `LDIR` (`0x035A`), then each test overwrites its own field. Reversing
every writer gives the layout:

| Offset | Addr | Bytes | Field (filled by) |
|---|---|---|---|
| `+0x00` | `0x9A99` | 2  | leading marker / header (`0x036D`) |
| `+0x02` | `0x9A9B` | 12 | **RAM test** result row — 6 cells (`0x092D`) |
| `+0x0E` | `0x9AA7` | 8  | **peripheral status** row (`0x089D`) |
| `+0x16` | `0x9AAF` | 8  | **support-chip (CTC) map** — one cell per port `0x50–0x53`, `0x60–0x63` (`0x0C81…0x0CC0`) |
| `+0x1E` | `0x9AB7` | 16 | **ROM checksum** display, `=`-separated hex (`0x09E5` + format `0x0A0A`) |
| `+0x2E` | `0x9AC7` | 1  | peripheral loopback result byte (`0x07F9`/`0x0810`) |
| `+0x2F` | `0x9AC8` | 1  | status byte (`0x0439`) |
| `+0x30` | `0x9AC9` | 2  | trailing `=` field (`0x0A35`) |

The fill bytes are literal ASCII for a character display: `0x3F`=`?`, `0x3D`=`=`, `0x20`=space,
`0x4B`=`K`. They line up exactly with the template strings at `0x05B7` (`"0?=?="`, `"7====????=…"`)
and the `"ROM,RAM ?"` / `"MPX KEYB OPDS CLDS FISC C"` labels — so the technician's display is
assembled by stamping each test's verdict over a `?`-initialised template, and that finished
image is also what a host gets back when it polls the MPX for status. One buffer, two audiences.

---

## 8. Open questions / next passes

* **`0x50`/`0x60` chips — resolved as Z80 CTCs** (§4/§5/§6.7): baud time-constants written to
  `0x5x`, IM-2 vector bases `0x50`/`0x58`. The one remaining detail is *which CTC channel
  clocks which SIO channel* — a board trace.
* **Channel index → peripheral name:** the dispatch map (§6.3) is confirmed, but which numeric
  index is KEYB vs OPDS vs CLDS vs FISC is set during runtime enumeration — trace the code
  that writes the channel-enable flags to pin the binding precisely.
* **The 50-byte status block at `0x9A99` — resolved** (§7.4): it doubles as the service-mode
  display image; field layout (RAM / peripheral / CTC-map / ROM-checksum / status rows) mapped.
* **Baud table (`0x432D`) — decoded** (§6.7) to relative 2:1 steps; converting to absolute
  rates just needs the **CTC clock crystal** measured on the board.
* **Map A/B flag — resolved** (§6.3): `0x119A` reads `0x9A91`, set by the boot presence test
  on the optional SIO at base `0x20`; absent board ⇒ Map B ⇒ channels 3 & 4 off.
* **Exact WR4 framing — decoded** (§6.7): reset default `WR4=0x47` (×16), reconfig
  `WR4=0xC4` (×64, 1 stop, no parity). Worth confirming against a logic-analyzer capture of
  the live bus.
* **ROM checksum — resolved** (§7.2): 16-bit position-weighted sum over `0x0000–0x7FFC`,
  stored at `0x7FFE-0x7FFF`, which is **blank (`FF FF`) in this dump** (computed `0x35FC`).
  The image looks like an un-finalised burn.

### 8.1 Hardware confirmations (and what's still open)

**Confirmed from the board** (folded into the sections above):

* **8 MHz crystal ÷2 → 4 MHz bus** (CPU + SIO/0 + DARTs + CTCs; the SIO/0 is a 4 MHz-rated
  `Z0844004`) → gives the exact baud ladder `1200…19200` (§6.7) and the 13.3 ms tick (§5.2).
* **One Z80 SIO/0 + three Z80 DART + two Z80 CTCs** → matches the firmware's I/O map exactly
  (§4). The DARTs are async-only; the lone SIO/0 (on the display/keyboard channels, not the host)
  has unused sync capability — v2.1 runs everything async. The *fifth* SIO base (`0x20`) is the
  **unpopulated expansion connector**, the very socket the Map-A/B presence test gates on (§6.3).
* **I/O decode = 74LS138** (A4/A5/A6, enabled by A7-low + `/IORQ` + `/M1`-high) → schematic CE
  labels confirm the map: `SIO_CE` (`0x40`) = SIO/0 (Cl Displ + Keyboard), `EXP_13` (`0x20`) =
  expansion socket, `DART1/2/3_CE` (`0x00/0x10/0x30`), `CTC1/2_CE` (`0x50/0x60`) (§4).
* **Roles confirmed by board traces + RX-ISR analysis** (§6.8): **Cl Displ** = SIO/0 pin 15 →
  74LS06; **Op. Displ** = DART3 pin 15 → 74LS06 (clocked by DART3 ch.B pin 27→14); **Keyboard** =
  SIO/0 ch.B (port `0x42`, via 74LS14); **host link** = DART1 ch.B (port `0x02`); the other DART
  inputs (`0x00/0x10/0x12/0x20/0x22`) feed the shared to-host ring (`0x99EA`).
* **RAM = 62256 (32 KB SRAM), decoded at `0x8000–0xBFFF` only** by a **74LS139** page decoder
  (A14/A15 + `/MREQ`; RAM `/CS` = pin 10 / `Y2`). The chip's upper 16 KB is unreachable because
  `/CS` only fires while A14=0; `0xC000–0xFFFF` (`Y3`) holds the `0xE000`/`0xF000` latches —
  a **74LS174 + 74LS175** (§2/§6.6).
* **Expansion** (§6.3): the three blanked connectors (**Serial 2, Serial 3, Drawer B**) are
  exactly the three expansion-gated features; `0xE000`/`0xF000` = Drawer A/B.

**Still genuinely open (mostly board-side):**

1. **Test mode — resolved by live capture** (§10.5). As wired (all channel-B `/DCD` grounded),
   the unit **boots into test mode** — confirmed by capturing the `O R S 500 (TEST)` banner on the
   Cl Displ line. So grounded `/DCDB` = test (an earlier draft had the polarity reversed). No mod
   needed for diagnostics; to force *normal* mode instead, lift **SIO/0 pin 31** (`/DCDB`) off GND
   and pull **HIGH**. Connectors are proprietary (likely AMP Amplimite D-subs), so any signal work
   is best done at the chip.
2. **The 74LS174 + 74LS175 latch outputs** (`0xE000`/`0xF000`, §6.6) → **10 latched output bits,
   only ~2 used** (Drawer A/B). The other ~8 drive unknown loads — beeper, watchdog, status LEDs,
   relay/power lines are the likely candidates (the boot self-check pokes both latches at
   `0x01E8`/`0x01FE`, so ≥1 bit is a heartbeat/status output). Worth tracing the Q pins. *(The
   second LS139's `Y0`/`Y1` at `0xC000–0xDFFF` are **not** an input port — the firmware never reads
   that range.)*
3. **Which SIO0/SIO1 channel is Keyboard vs Printer vs Aut-Inp vs Serial 1** → *not a firmware
   fact* (§6.8): the channels are identical templates and the MPX is device-agnostic, so this
   is purely the connector-panel wiring. Reading the panel/backplane is the only way to pin it.
   (Displays and VID **are** firmware-confirmed via the service-display output ports.)
4. **What "Aut-Inp" is** → an auxiliary input device (scanner? scale? mag-stripe?) — identify
   from the connector pinout or by what protocol the host runs on that channel.

---

## 9. Reproducing this analysis

Everything here is reproducible from the bytes with the small disassembler committed
alongside this doc:

```sh
# full linear disassembly
python tools/z80dis.py Olivetti_MX08.bin 0 0x8000 > docs/Olivetti_MX08_full_disasm.txt

# a specific routine (start and end offsets, hex)
python tools/z80dis.py Olivetti_MX08.bin 0x44C8 0x4510
```

Artifacts in this folder:

| File | What |
|---|---|
| `Olivetti_MX08.bin` | the 32 KiB EPROM dump |
| `tools/z80dis.py` | self-contained Z80 disassembler used for this writeup |
| `docs/Olivetti_MX08_full_disasm.txt` | full linear disassembly (`0x0000–0x7FFF`) |
| `docs/MX08_firmware_analysis.md` | this document |

*Note on the linear disassembly:* it decodes data regions (jump tables, strings, blank
`0xFF`) as if they were code, so treat runs of `RST $38`, `LD` salad, and the `0x4140`/
`0x4334` table areas as **data**, not instructions. The annotated routines in this document
are the trustworthy ones.

---

## 10. Appendix: sniffing the display / "video" protocol

Because the `VID` and display outputs are plain async serial (§6.9), the data they carry can
be captured and decoded with nothing more than a serial sniffer. The firmware tells us the
protocol up front.

### 10.1 The control codes

The display strings in ROM (banner at `0x0600`, status templates at `0x05B7`) use a tiny
control set:

| Byte | Name | Meaning |
|---|---|---|
| `0x12` | DC2 | **clear / home** — sent at the start of each screen refresh |
| `0x09` | HT  | **tab** — field / column positioning |
| `0x0D` | CR  | **end of line** |
| `0x20–0x7E` | — | printable ASCII characters |
| `0xFF` | — | **internal end-of-string marker — NOT transmitted**; the send loop (`0x0564`) stops on it |

So a refresh looks like `12 ...ASCII... 09 ...ASCII... 0D` — e.g. the test banner is literally
`12 "  O R S 500 (TEST)" 09 "ROM,RAM ?" 0D`. An "interpreter" is therefore a four-case state
machine, not a terminal emulator.

### 10.2 The physical tap

Both character displays are **`TxDA` = pin 15** of their chip (clean TTL, idle-high), feeding
different gates of one shared 74LS06:

* **Cl Displ** — **SIO/0 pin 15** (`SIO_CE` = `0x40`, §4) → 74LS06 gate 2 (3→4)
* **Op. Displ** — **DART3 pin 15** (`DART3_CE` = `0x30`) → 74LS06 gate 5 (11→10)

(The `0x30` chip's ch.B `TxDB` = pin 27 is **not** a display — it's wired to ch.A's `/TxCA`
pin 14 as ch.A's transmit clock, §6.8, so don't sniff there; scope it and you'll see a clock,
not text.)
Framing is **8-N-1**, and the display baud is **confirmed 9600** by live capture (§10.5) —
bit cell 104 µs, idle-high at pin 15.

* **Tap at pin 15 (recommended).** The SIO `TxDA` pin is 5 V TTL, **idle-high, correct UART
  polarity** → straight to a 5 V Arduino RX, **common ground**, sniff only (leave your TX
  disconnected). This is the clean tap.
* **Don't tap the 74LS06 output** for a TTL UART. The display line driver here is a **`74LS06`
  hex inverter (open-collector)** — SIO pin 15 → 74LS06 pin 3 in / pin 4 out → connector — so
  the connector signal is **inverted** (and OC). Feeding that into a UART gives garbage unless
  you re-invert it (a single transistor or another inverter). Not RS-232 (no ±12 V), so no
  MAX3232 needed — just tap pin 15.
* Use a board with a **spare UART**: Mega (`Serial1`), Leonardo/Micro (`Serial1`), or ESP32
  (`Serial2`, 3.3 V — level-shift the 5 V TTL). For a raw dump, a USB-TTL adapter into a PC
  terminal is enough.

### 10.3 Minimal interpreter (Arduino Mega)

```cpp
// Sniff MX-08 VID/display serial on Serial1, annotate to the USB serial monitor.
void setup() {
  Serial.begin(115200);   // to PC
  Serial1.begin(9600);    // tapped line — try 9600 then 19200
  Serial.println(F("--- MX-08 display sniffer ---"));
}
void loop() {
  if (!Serial1.available()) return;
  uint8_t b = Serial1.read();
  switch (b) {
    case 0x12: Serial.println(F("\n[CLEAR/HOME]")); break;  // DC2
    case 0x09: Serial.print(F(" | "));               break;  // tab/field
    case 0x0D: Serial.println();                     break;  // end of line
    default:
      if (b >= 0x20 && b < 0x7F) Serial.write(b);
      else { Serial.print('<'); Serial.print(b, HEX); Serial.print('>'); }
  }
}
```

### 10.4 What you'll capture (mode matters)

* **TEST mode** (§3): the MPX **generates its own** `O R S 500 (TEST)` / `ROM ?` / `RAM ?`
  screens. As wired (all channel-B `/DCD` grounded), the unit **boots straight into test mode**
  (§3, §10.5), so this is what you get standalone — no host, no board mod.
* **Normal operation** (would require lifting `/DCDB` high): screen content is host-originated —
  the MPX is a pipe (§6.8), so the display carries whatever the host sends. Live transaction
  screens need the host driving the link (or you replaying host commands into port `0x02`).

### 10.5 Confirmed by live capture (Cl Displ, power-on)

A logic-analyzer trace of **SIO/0 pin 15** (`Cl Displ`, `TxDA`) at startup decodes cleanly and
nails the protocol and the boot mode:

* **9600 baud, 8-N-1, idle-high** (bit cell 104 µs = the `TC=26` baud-table entry, §6.7 — *not*
  19200). UART, LSB-first, normal polarity at pin 15 (the 74LS06 output is inverted, §6.9).
* Decoded bytes:

  ```
  09 20 20 4F 20 52 20 53 20 35 30 30 20 28 54 45 53 54 29 09 4B 45
  → [TAB] "  O R S 500 (TEST)" [TAB] "KE…"
  ```

  i.e. the **`O R S 500 (TEST)` banner** (`0x0610`) + `0x09` field separator + the start of the
  `KEYB OPDS CLDS FISC C` peripheral list (§1). The `0x09` (TAB) control code behaves exactly as
  §10.1 predicts.
* **The `(TEST)` banner proves the test executive is running** → `RR0 bit 3 = 0` at reset →
  confirms grounded `/DCDB` selects TEST mode (the polarity correction in §3). The unit drives
  the display standalone with no host attached.
