# Changelog

<!--next-version-placeholder-->

## v0.4.3 (2025-05-26)

### Fix

* Check if snapshot exists before running reset sequence ([`81e62c8`](https://github.com/educationwarehouse/edwh-devdb-plugin/commit/81e62c8832f8d8925d79d5fbb442267b4b8ff132))

## v0.4.2 (2025-05-01)

### Fix

* Snapshot-full should work again ([`a844ff7`](https://github.com/educationwarehouse/edwh-devdb-plugin/commit/a844ff7e0c5304c0a7eef09b959d5b87c0704982))

## v0.4.1 (2025-04-25)

### Fix

* Pass `--yes` to `devdb.pop` when running `devdb.reset --pop` ([`7e29ebb`](https://github.com/educationwarehouse/edwh-devdb-plugin/commit/7e29ebb6661adc2094deb2ccf518b1fc854f8ba0))

## v0.4.0 (2025-03-21)

### Feature

* **snapshot:** Replace hardcoded `--exclude-*` with config from .toml or manual override (e.g. `--exclude public.table`) ([`88130f4`](https://github.com/educationwarehouse/edwh-devdb-plugin/commit/88130f4e9067582cda820c91d0f5cc3d86230590))

## v0.3.1 (2025-03-21)

### Fix

* Don't hook `setup` by default (we don't always want `POSTGRES` for every `setup`) ([`858fba4`](https://github.com/educationwarehouse/edwh-devdb-plugin/commit/858fba4df35dcdaa0c6ebb1881c528861a648208))

## v0.3.0 (2025-03-17)

### Feature

* **reset:** Add `--pop` option to download a backup before resetting ([`969fdba`](https://github.com/educationwarehouse/edwh-devdb-plugin/commit/969fdba6b2d905791fcdacd912fffa8bf440ad9f))

## v0.2.0 (2025-03-15)

### Feature

* Allow `--compress` in `devdb.snapshot`, determine amount of threads automatically (cores - 1) instead of hard-coded (3) ([`5817f1c`](https://github.com/educationwarehouse/edwh-devdb-plugin/commit/5817f1c048b6b6c0f5cf1be69c94d5240f8d1d3a))

## v0.1.1 (2025-03-07)

### Documentation

* Added README ([`dd61687`](https://github.com/educationwarehouse/edwh-devdb-plugin/commit/dd61687562482dca3be57ec9bcedc18083a52ead))

## v0.1.0 (2025-03-07)

### Feature

* Initial devdb extracted from our core; included `setup` which hooks after global `edwh setup` ([`0ba6cbd`](https://github.com/educationwarehouse/edwh-devdb-plugin/commit/0ba6cbde9724b32a6259a755dbbcaf8f5caa8301))