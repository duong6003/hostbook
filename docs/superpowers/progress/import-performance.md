# Import preflight timing

Revision `17f9bce0353b47defedec532f5ae16fd2b0625aa`, macOS 27.2 arm64, private tmux at 100×24, English and rounded border. Five samples per size followed one discarded warmup; each measured sample used a fresh synthetic fixture.

| Segment | 30 hosts median (range), ms | 500 hosts median (range), ms |
| --- | ---: | ---: |
| CLI launch to visible preflight | 922.0 (899.0–954.2) | 8,924.3 (8,842.8–10,290.4) |
| Import content staging to review entry | 579.1 (569.3–631.7) | 6,619.7 (6,531.1–7,867.1) |
| Review entry to rendered screen entry | 176.5 (171.3–180.9) | 2,158.9 (2,153.1–2,264.5) |
| Screen entry to first visible preflight | 48.0 (46.7–55.7) | 51.1 (46.2–68.0) |
| Review entry to first visible preflight | 228.9 (218.5–232.2) | 2,220.6 (2,199.3–2,320.3) |

Raw samples and percentile summaries: `import-performance.json`. The screen-entry measurement includes tmux capture and polling: measured capture subprocess median 5.50 ms at 30 hosts and 5.44 ms at 500 hosts, with 5 ms polling after the screen-entry marker. These results are not a pure renderer benchmark or a before/after comparison. The measured 500-host wait is predominantly staging and review preparation for this synthetic fixture; this benchmark alone does not identify individual causes.

The harness archives the fixed revision and adds three shell-builtin marker writes only to that temporary copy, at `import_content`, `import_review`, and `import_review_screen`. Staging begins after the input file has been copied into the import flow; launch-to-visible includes startup and source copy. Marker intervals use filesystem timestamps; visible detection uses wall-clock observation after capture. Fixtures contain no credentials or keys. A wrapper permits only offline `ssh -G`, and the private host directory is checked for writes before teardown. No real host, personal HOME, network connection, or user tmux server was used. Benchmark command:

```sh
python3 tests/import-performance.py --revision 17f9bce --samples 5 --warmups 1 --output /tmp/hostbook-import-performance.json
```

No production files were modified. Linux, Windows, physical terminals, other source types and sizes are not covered; per-key performance was not measured.
