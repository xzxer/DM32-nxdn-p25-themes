#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 lyra XE1POC
"""Build the DM-32UV firmware: RadioFoundry at a pinned public commit plus the patches in integration/current (the DM32
fork: mode row with AM, extended RX, digital monitor on P25/NXDN, AES-256 for P25/NXDN). Never opens a radio and never
fetches the AMBE codec.

    python3 tools/build_current.py [--tree build/current] [--cross PREFIX] [--jobs N]

The image is TREE/build/dm32/rfoundry_dm32.bin; package it with TREE/tools/release.py.
"""
import argparse
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import shlex

ROOT = Path(__file__).resolve().parents[1]
PIN = 'e89121e97a314f0ddd0475f848713ba10710d7db'
UPSTREAM = 'https://github.com/RadioFoundry/RadioFoundry.git'
PATCH_DIR = ROOT / 'integration/current'
VARIANT_DIR = ROOT / 'integration/variants'
RELEASE_VARIANTS = ('full', 'multimode', 'dmr-fm')
OPTIONAL_MODULES = {
	'aprs': 'APRS', 'dstar': 'DSTAR', 'm17': 'M17', 'mdc1200': 'MDC1200',
	'nxdn': 'NXDN', 'p25': 'P25', 'rssi-graph': 'RSSI_GRAPH', 'ysf': 'YSF',
}
EXPERIMENTAL_MODULES = {'monitor-scan': 'MONITOR_SCAN', 'cw': 'CW'}
PATCHES = (
	'base.patch', 'channel-aes-ysf.patch', 'vfo-rx-bands.patch', 'ysf-tx-pcm.patch',
	'ysf-vfo-identity.patch',
	'vfo-digital.patch',
	'dmr-motorola-privacy.patch',
	'digital-repeater-interop.patch',
	'ysf-dn-narrow.patch',
	'ysf-group-dest.patch',
	'ysf-callsign-fich.patch',
	'fast-scan.patch',
	'unified-tx-splash.patch',
	'radiofoundry-660-audio-fifo.patch',
	# MDC-1200 ID on FM channels (the firmware, then the web editor); off unless CONFIG_MDC1200=y
	'mdc1200-ptt-id.patch',
	'mdc1200-decode.patch',
	'mdc1200-editor.patch',
	# Per-channel FM squelch, including an explicit always-open level 0
	'channel-squelch.patch',
	# Bulk Edit RX Only: apply to every selected row and close without a false Boolean error
	'rxonly-bulk-edit.patch',
	# Bulk Edit: keep Field and Value in labelled form rows, not underneath their selectors
	'bulk-edit-dialog.patch',
	# Quick menu: NAC/RAN/DG-ID and talk group/destination selectors on P25, NXDN and YSF channels
	'quick-digital-selectors.patch',
	# Host tests: the VFO AES flows need CONFIG_AIR_AES (the NoColor build has no air modes)
	'nocolor-aes-flows.patch',
	# DMR: channel-local recent activity, TX hang time, and correct burst-F PI for ARC4/AES late entry
	'dmr-activity-hang-late-entry.patch',
	# Tools: independent, persistent zone-scan dwell; FM 50 ms, digital at least 75 ms
	'zone-scan-speed.patch',
	# DMR scan: discard an unsynchronized carrier after 450 ms; valid sync keeps full late-entry time
	'dmr-scan-probe-speed.patch',
	# Air band AM: use stock's AM audio gain instead of FM's word, and restore FM on exit
	'am-airband-af-gain.patch',
	# P25: alternate the IMBE frame-sync bit through LDU1/LDU2, before FEC and AES
	'p25-imbe-sync.patch',
	# Zone scan: empty digital channels advance after 50 ms; signal probes stay unchanged
	'zone-digital-scan-50ms.patch',
	# Zone scan: 25 ms settle and empty-channel dwell; VFO and dual watch keep their timing
	'zone-scan-25ms.patch',
	# P25/YSF VW: calibrate the software IMBE microphone input to the reference encoder's level
	'p25-tx-voice-level.patch',
	# RadioFoundry #856: dual-watch DMR row fits the full color code beside the PTT pill
	'radiofoundry-856-dual-watch.patch',
	# NXDN dual watch: retain independent digital-monitor latches across same-mode retunes
	'nxdn-dual-monitor.patch',
	# YSF: receive the call's actual AMBE/IMBE mode and restore the configured TX voice path
	'ysf-cross-dt-rx.patch',
	# NXDN96: admit trunked RTCH voice; keep DMon on the selected channel and report its real state
	'nxdn-trunked-dmon.patch',
	# FD6818: clear the FM receive sub-audio on entering DMR; a toned FM channel left it keyed on every digital TX
	'digital-tx-subaudio-off.patch',
	# Codec 2 (LGPL-2.1, 3200 bit/s files only) and M17 stream voice as a per-channel mode; --no-m17 leaves it out
	'codec2-vendor.patch',
	'm17-mode.patch',
	# M17 TX: integer Codec 2 3200 analysis; reference floating-point RX and the 15 ms guard stay in place
	'm17-fixed-tx.patch',
	# DM-32UV: experimental FM RX through 1120 MHz, nine-digit GHz VFO entry, always RX-only
	'dm32-rx-1120.patch',
	# DM-32UV: optional extended RX in Tools, off until the owner enables it and persistent across restarts
	'dm32-rx-tools.patch',
	# CPS CSV: keep conventional NXDN channels as NXDN (rate, RAN and squelch), not FM
	'nxdn-csv-mode.patch',
	# P25 TX (and YSF VW, same voice path): 4:1 soft limiter above a 3/4-scale knee instead of hard clipping
	'p25-tx-limiter.patch',
	# User guide: the ninth VFO digit for GHz RX, and the Tools > RX past bands gate that refuses it while Off
	'vfo-ghz-entry-doc.patch',
	# RX past bands: every mode there (VFO and channels), and GHz channel records with a 0xA/0xB marker nibble
	'rx-ext-ghz-channels.patch',
	# NXDN48/NXDN96 Type-C trunking: follow voice grants, register and request calls on TX-enabled channels
	'nxdn-trunking.patch',
	# The APRS preset uses a new ID so saved Custom settings remain compatible.
	'aprs-brazil.patch',
	# Preserve Fusion channel settings in CSV and use the same deviation rule as the radio.
	'ysf-csv-mode.patch',
	'ysf-cps-deviation.patch',
	'ysf-csv-interop.patch',
	# Restore the selected channel's digital monitor before starting its modem.
	'dual-vfo-monitor.patch',
	'dual-air-monitor.patch',
	'dual-watch-dwell.patch',
	'battery-voltage.patch',
	'scan-carrier-and-persistence.patch',
	'broadcast-fm-scan.patch',
	'gps-position-format.patch',
	'dual-dmr-idle-activity.patch',
	'voice-privacy-indicators.patch',
	'large-channel-name.patch',
	'nxdn-800-diagnostics.patch',
	'issue76-dual-digital-busy.patch',
	'issue63-zone-dwell-observability.patch',
	'issue66-nxdn-dual-monitor-tests.patch',
	'issue26-gps-motion-freshness.patch',
	'issue75-aprs-brazil-menu.patch',
	'issue69-preserve-fusion-mode.patch',
	'issue51-voice-privacy-lock.patch',
	'issue54-ui-settings.patch',
	'issue54-startup-image.patch',
	'issue32-spanish-name-option.patch',
	'issue24-broadcast-fm-vfo.patch',
	'issue53-aes-bulk.patch',
	'issue61-p25-talkgroup.patch',
	'issue33-dmr-dial.patch',
	'issue50-tone-search.patch',
	'issue53-home-display.patch',
	'issue47-dmr-aes128.patch',
	'issue53-lrrp-position.patch',
	'issue61-p25-des.patch',
	'issue53-flasher-startup.patch',
	'issue41-nxdn-scrambler.patch',
	'issue61-des-combined-dropdown.patch',
	'issue53-clear-voice.patch',
	'issue53-zh-glyphs.patch',
	'issue76-dual-preserve-decoded-voice.patch',
	'issue29-p25-rx-wrap.patch',
	'remaining-user-features.patch',
	'dm32-ram-defaults.patch',
	'tier3-codeplug-rx.patch',
	'optional-access-pins.patch',
	# DM-32UV issue regressions: keep scan scheduling responsive and refresh FM, GPS and YSF receive state.
	'issue85-scan-watchdog.patch',
	'issue82-fm-squelch.patch',
	'issue81-gps-session.patch',
	'issue79-ysf-caller.patch',
	'issue80-display-cps.patch',
	'low-band-nfm.patch',
	'air-fm-capture-handoff.patch',
	'hrc7000-air-copy-recovery.patch',
	'dm32-tools-menu.patch',
	'issue79-ysf-channel-deviation.patch',
	'dm32-vfo-broadcast.patch',
	'ysf-caller-crc.patch',
)


def run(*args, cwd=None):
	subprocess.run([str(a) for a in args], cwd=cwd, check=True)


def out(*args, cwd=None):
	return subprocess.check_output([str(a) for a in args], cwd=cwd, text=True).strip()


def prepare(tree, dstar=False):
	if not (tree / '.git').exists():
		tree.mkdir(parents=True, exist_ok=True)
		if any(tree.iterdir()):
			raise SystemExit(f'{tree} is not empty: choose a separate build tree')
		run('git', 'init', '-q', tree)
		run('git', 'config', 'core.autocrlf', 'false', cwd=tree)
		run('git', 'remote', 'add', 'origin', UPSTREAM, cwd=tree)
		run('git', 'fetch', '-q', '--depth=1', 'origin', PIN, cwd=tree)
		run('git', 'checkout', '-q', '--detach', PIN, cwd=tree)
	# Always from the pin: a tree left by an earlier build is reset, so the image is the patch's and nothing else
	if out('git', 'rev-parse', 'HEAD', cwd=tree) != PIN:
		raise SystemExit('%s is not at %s: remove it and build again' % (tree, PIN))
	run('git', 'reset', '-q', '--hard', PIN, cwd=tree)
	run('git', 'clean', '-q', '-fdx', '-e', 'build/', cwd=tree)
	for patch in PATCHES:
		run('git', 'apply', '--binary', PATCH_DIR / patch, cwd=tree)
	if dstar:
		run('git', 'apply', '--binary', PATCH_DIR / 'dstar-mode.patch', cwd=tree)
		run('git', 'apply', '--binary', PATCH_DIR / 'dstar-channels.patch', cwd=tree)
		run('git', 'apply', '--binary', PATCH_DIR / 'dstar-hotspot-routing.patch', cwd=tree)
		run('git', 'apply', '--binary', PATCH_DIR / 'dstar-channel-editor.patch', cwd=tree)
		run('git', 'apply', '--binary', PATCH_DIR / 'dstar-gmsk-rx.patch', cwd=tree)
		shutil.copytree(ROOT / 'src/dstar', tree / 'core/dstar')
	run('git', 'apply', '--binary', PATCH_DIR / 'rssi-graph.patch', cwd=tree)
	run('git', 'apply', '--binary', PATCH_DIR / 'scan-delay-monitor-fields.patch', cwd=tree)
	run('git', 'apply', '--binary', PATCH_DIR / 'dmr-roger.patch', cwd=tree)
	run('git', 'apply', '--binary', PATCH_DIR / 'scan-monitor-roger-tests.patch', cwd=tree)
	run('git', 'apply', '--binary', PATCH_DIR / 'dm32-preset-stacks.patch', cwd=tree)
	run('git', 'apply', '--binary', PATCH_DIR / 'mbe-q15-speed.patch', cwd=tree)
	if dstar:
		run('git', 'apply', '--binary', PATCH_DIR / 'dstar-voice-deadline.patch', cwd=tree)
	run('git', 'apply', '--binary', PATCH_DIR / 'vfo-ysf-label.patch', cwd=tree)
	run('git', 'apply', '--binary', PATCH_DIR / 'vfo-ysf-voice.patch', cwd=tree)
	run('git', 'apply', '--binary', PATCH_DIR / 'vfo-ysf-quick-data-type.patch', cwd=tree)
	run('git', 'apply', '--binary', PATCH_DIR / 'vfo-scan-limits-and-digital-sweep.patch', cwd=tree)
	run('git', 'apply', '--binary', PATCH_DIR / 'dmr-fast-empty-scan.patch', cwd=tree)
	run('git', 'apply', '--binary', PATCH_DIR / 'low-band-fm-bandwidth.patch', cwd=tree)
	if dstar:
		run('git', 'apply', '--binary', PATCH_DIR / 'dstar-quick-width.patch', cwd=tree)
	run('git', 'apply', '--binary', PATCH_DIR / 'dm32-bench-tx-1p25m.patch', cwd=tree)
	run('git', 'apply', '--binary', PATCH_DIR / 'vfo-am-rx.patch', cwd=tree)
	run('git', 'apply', '--binary', PATCH_DIR / 'ysf-auto-vfo-deviation.patch', cwd=tree)
	run('git', 'apply', '--binary', PATCH_DIR / 'dm32-satcom-rx-memories.patch', cwd=tree)
	run('git', 'apply', '--binary', PATCH_DIR / 'release-decode-captures.patch', cwd=tree)
	run('git', 'apply', '--binary', PATCH_DIR / 'release-mmdvm-captures.patch', cwd=tree)
	if dstar:
		run('git', 'apply', '--binary', PATCH_DIR / 'release-decode-dstar.patch', cwd=tree)
		run('git', 'apply', '--binary', PATCH_DIR / 'issue108-dstar-mic-gain.patch', cwd=tree)
	run('git', 'apply', '--binary', PATCH_DIR / 'issue105-channel-tx-contact.patch', cwd=tree)
	run('git', 'apply', '--binary', PATCH_DIR / 'fm-mdc-roger.patch', cwd=tree)
	run('git', 'apply', '--binary', PATCH_DIR / 'signaling-tones.patch', cwd=tree)
	run('git', 'apply', '--binary', PATCH_DIR / 'issue117-tx-mic-gain.patch', cwd=tree)
	run('git', 'apply', '--binary', PATCH_DIR / 'dm32-color-startup-image.patch', cwd=tree)
	if dstar:
		run('git', 'apply', '--binary', PATCH_DIR / 'issue117-dstar-mic-gain-tests-docs.patch', cwd=tree)
	for name in RELEASE_VARIANTS[1:]:
		shutil.copyfile(VARIANT_DIR / (name + '.mk'), tree / 'config/variants' / (name + '.mk'))
	run('git', 'apply', '--binary', PATCH_DIR / 'issue120-module-config.patch', cwd=tree)
	for header in ('monitor_cw.h', 'monitor_session.h'):
		shutil.copyfile(ROOT / 'experimental/monitor-cw' / header, tree / 'core/radio' / header)
	for patch in ('monitor-scan-runtime.patch', 'issue120-cw-envelope.patch', 'issue120-monitor-cw-ui.patch',
	              'monitor-scan-air-validation.patch'):
		run('git', 'apply', '--binary', PATCH_DIR / patch, cwd=tree)
	run('git', 'apply', '--binary', PATCH_DIR / 'dm32-kiss-tnc-afsk.patch', cwd=tree)
	run('git', 'apply', '--binary', PATCH_DIR / 'nxdn-scan-call-replay.patch', cwd=tree)
	run('git', 'apply', '--binary', PATCH_DIR / 'issue121-aprs-dual-watch.patch', cwd=tree)
	run('git', 'apply', '--binary', PATCH_DIR / 'issue119-tier3-rx.patch', cwd=tree)
	# Tactical visual style (white phosphor palette and reticle boot screen): last, so upstream patches stay untouched
	run('git', 'apply', '--binary', PATCH_DIR / 'tactical-white-phosphor.patch', cwd=tree)
	run('git', 'submodule', 'update', '--init', '--depth=1', 'third_party/FreeRTOS-Kernel', cwd=tree)


def check_monitor_cw():
	"""Run portable RX scan and Morse tests using a native compiler, not C-SKY."""
	root = ROOT / 'experimental/monitor-cw'
	with tempfile.TemporaryDirectory(prefix='dm32-monitor-cw-') as tmp:
		binary = Path(tmp) / 'monitor-cw-test'
		run(*shlex.split(os.environ.get('HOSTCC', 'cc')), '-std=c11',
		    '-Wall', '-Wextra', '-Werror', '-pedantic', '-Os',
		    root / 'test.c', '-o', binary)
		run(binary)


def main():
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument('--tree', type=Path, default=ROOT / 'build/current',
	                help='dedicated build directory (must be empty on first use)')
	ap.add_argument('--cross', default=os.environ.get('CROSS', str(ROOT / 'toolchain/bin/csky-elf-')))
	ap.add_argument('--jobs', type=int, default=min(os.cpu_count() or 2, 8))
	ap.add_argument('--variant', choices=RELEASE_VARIANTS, default='full', help='firmware preset')
	ap.add_argument('--without', action='append', choices=OPTIONAL_MODULES, default=[], metavar='MODULE',
	                help='omit a module (repeatable): ' + ', '.join(OPTIONAL_MODULES))
	ap.add_argument('--with', dest='with_modules', action='append', choices=EXPERIMENTAL_MODULES, default=[],
	                metavar='EXPERIMENTAL_MODULE',
	                help='enable an experimental module (repeatable): ' + ', '.join(EXPERIMENTAL_MODULES))
	ap.add_argument('--dstar', action=argparse.BooleanOptionalAction, default=True,
	                help='build experimental D-STAR DV for the VFO and stored channels (RF unverified)')
	ap.add_argument('--no-m17', action='store_true', help='build with CONFIG_M17=n (M17 voice; Codec 2 real time on the MCU unverified)')
	ap.add_argument('--no-mdc1200', action='store_true', help='build with CONFIG_MDC1200=n (MDC-1200 ID on FM channels)')
	ap.add_argument('--bench-tx-1p25m', action='store_true',
	                help='write the 220–230 MHz bench build with a separate name and output directory')
	ap.add_argument('--monitor-cw-tests-only', action='store_true',
	                help='run native Monitor Scan/CW regression tests without fetching or building firmware')
	args = ap.parse_args()
	check_monitor_cw()
	if args.monitor_cw_tests_only:
		return
	tree = args.tree.resolve()
	disabled = set(args.without)
	if not args.dstar or args.variant != 'full' or 'p25' in disabled:
		disabled.add('dstar')
	if args.no_m17 or args.variant != 'full' or {'p25', 'nxdn', 'ysf'} <= disabled:
		disabled.add('m17')
	if args.no_mdc1200 or 'aprs' in disabled:
		disabled.add('mdc1200')
	dstar = 'dstar' not in disabled
	prepare(tree, dstar)
	# The version is this repository's commit, the time its commit time: the patched tree is never committed
	version = out('git', 'rev-parse', '--short=8', 'HEAD', cwd=ROOT)
	git_time = out('git', 'log', '-1', '--format=%ct', 'HEAD', cwd=ROOT)
	# Reserve more main RAM for the private D-STAR build while keeping the host link and APRS enabled.
	dstar_options = ('CONFIG_NET_HTTP=n', 'CONFIG_SFR=n') if dstar else ()
	variant = ('VARIANT=' + args.variant,) if args.variant != 'full' else ()
	bench_options = ()
	if args.bench_tx_1p25m:
		variant_suffix = '' if args.variant == 'full' else '-' + args.variant
		bench_options = (f'BUILD=build/dm32{variant_suffix}-bench-1p25m',)
		version += '-bench-1p25m'
	custom = args.without or args.with_modules or not args.dstar or args.no_m17 or args.no_mdc1200 or bench_options
	preset = 'custom' if custom else args.variant
	options = {name: 'n' for key, name in OPTIONAL_MODULES.items() if key in disabled}
	options['M17'] = 'n' if 'm17' in disabled else 'y'
	options['MDC1200'] = 'n' if 'mdc1200' in disabled else 'y'
	options['DSTAR'] = 'y' if dstar else 'n'
	options.update({name: 'y' if key in args.with_modules else 'n' for key, name in EXPERIMENTAL_MODULES.items()})
	run('make', '-j%d' % args.jobs, 'BOARD=dm32', 'CROSS=' + args.cross, 'GITVERSION=' + version,
	    'GITDIRTY=', 'RFOUNDRY_GIT_TIME=' + git_time, 'RFOUNDRY_PRESET=' + preset, *variant, *bench_options,
	    *(f'CONFIG_{name}={value}' for name, value in options.items()), *dstar_options, 'all', 'stack-check', cwd=tree)


if __name__ == '__main__':
	main()
