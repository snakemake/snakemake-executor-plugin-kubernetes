# Changelog

## [0.4.1](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/compare/v0.4.0...v0.4.1) (2025-05-15)


### Bug Fixes

* output correct kubectl commands for changed submission approach (jobs instead of pods) ([#47](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/issues/47)) ([0337d1c](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/commit/0337d1c55ab2566ccd7cb35672c16ecce3e90483))

## [0.4.0](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/compare/v0.3.2...v0.4.0) (2025-04-04)


### Features

* use k8s job API and improve status check robustness in case of injected containers ([#43](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/issues/43)) ([1ff6927](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/commit/1ff6927d40794926e9a86b88ea41341f82079c95))


### Bug Fixes

* Added documentation for scale variable ([#40](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/issues/40)) ([1b668c1](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/commit/1b668c180f3b13c4d9bd7b8121834df8fd778cee))
* properly catch and report ApiExceptions ([#42](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/issues/42)) ([92375e6](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/commit/92375e64470887ace6fadccdcd4befba9deadf01))

## [0.3.2](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/compare/v0.3.1...v0.3.2) (2025-03-06)


### Bug Fixes

* Adding additional logic to handle resource limit requirements ([#38](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/issues/38)) ([25819c5](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/commit/25819c5ecd611960a60b3559b99aad4cb1fd3421))

## [0.3.1](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/compare/v0.3.0...v0.3.1) (2025-03-05)


### Bug Fixes

* fix name of gpu_manufacturer resource ([#35](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/issues/35)) ([3d6ed3a](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/commit/3d6ed3a887035d4d49053bc7d9ffeea7315d7f94))

## [0.3.0](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/compare/v0.2.2...v0.3.0) (2025-02-08)


### Features

* Added GPU node pool support for GKE, better logging, error and exception handling  ([#31](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/issues/31)) ([6b1cac9](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/commit/6b1cac9f72302573f30d1c59f8714f2a7e10cb8d))

## [0.2.2](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/compare/v0.2.1...v0.2.2) (2024-10-08)


### Bug Fixes

* [#27](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/issues/27) attribute overwriting envvars method in `RealExecutor` ([#28](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/issues/28)) ([d6c0bd5](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/commit/d6c0bd5afd4f64ce7ced4ea2217b216a71f7ae94))

## [0.2.1](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/compare/v0.2.0...v0.2.1) (2024-09-07)


### Performance Improvements

* update kubernetes requirement from &gt;=27.2.0,&lt;30 to >=27.2.0,<31 ([#20](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/issues/20)) ([046d06f](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/commit/046d06f03a16e88e95e1a62f1632f4baca13bcc5))

## [0.2.0](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/compare/v0.1.5...v0.2.0) (2024-08-15)


### Features

* PVC support for kubernetes (continuation of [#9](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/issues/9)) ([#22](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/issues/22)) ([33a6809](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/commit/33a680905866e197d2a8bcc5d1600827a4f77740))


### Bug Fixes

* handle cases when running inside a kubernetes cluster ([#17](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/issues/17)) ([a031314](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/commit/a0313140a24f72fc1c3a89e4eeea161f14dec1a2))

## [0.1.5](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/compare/v0.1.4...v0.1.5) (2024-03-12)


### Bug Fixes

* Allow kubernetes versions 28 and 29 ([#10](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/issues/10)) ([47a5f37](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/commit/47a5f375de532aa2b83712cedd311a8c978e3798))
* update snakemake-interface-executor-plugins requirement from ^8.0.2 to &gt;=9.0.0,&lt;10.0.0 ([#12](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/issues/12)) ([861c44f](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/commit/861c44f3c0a9e2eb1861beb4b0dcea1f02180ccc))

## [0.1.4](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/compare/v0.1.3...v0.1.4) (2023-12-08)


### Documentation

* update metadata ([c6cabd4](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/commit/c6cabd4e70bcb029f809c38434e9d74eea6b87ac))

## [0.1.3](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/compare/v0.1.2...v0.1.3) (2023-11-20)


### Bug Fixes

* adapt to interface changes ([ea27410](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/commit/ea27410827edac3bc599d2ed191fcb360a473ec6))

## [0.1.2](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/compare/v0.1.1...v0.1.2) (2023-10-27)


### Bug Fixes

* update dependencies ([#5](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/issues/5)) ([5914f33](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/commit/5914f33c24907836f18aa44441425cf4f42db7b4))

## [0.1.1](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/compare/v0.1.0...v0.1.1) (2023-10-17)


### Bug Fixes

* fix release process ([1861240](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/commit/1861240110afb8fcb1b00668a94947dce4ab7a47))


### Miscellaneous Chores

* release 0.1.1 ([a5ea315](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/commit/a5ea3154351fbfae993280165ec65f8d7c4b1d89))

## 0.1.0 (2023-10-17)


### Bug Fixes

* adapt to API changes ([6579f2e](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/commit/6579f2e143d638b16bdf2836896ca0f00f8c0016))
* adapt to changes in snakemake-interface-executor-plugins ([11528ea](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/commit/11528eaf975cc638cadd6405e7e8896a3a7c6fae))
* use correct jobid ([5373443](https://github.com/snakemake/snakemake-executor-plugin-kubernetes/commit/53734439cb7fdd2b65530e9c770fe6f17b7478bd))
