# Changelog

## 0.1.0 (2026-08-27)


### ⚠ BREAKING CHANGES

* **ingest:** follow django-api's format -> variant rename ([#13](https://github.com/Frikanalen/ingest/issues/13))

### Features

* **archive:** archive to another host over SSH ([0a02596](https://github.com/Frikanalen/ingest/commit/0a025961e55069279c9b2ab5a5c5d63f5099201b))
* **archive:** fall back to a local directory without SSH credentials ([39a1fa8](https://github.com/Frikanalen/ingest/commit/39a1fa839663557015e2a773adecc62d1f74d43f))
* **chart:** deploy ingest and tusd with Helm ([a4fb5cf](https://github.com/Frikanalen/ingest/commit/a4fb5cf73e85dd33c8f170c15bc5493fbcd888ec))
* **ingest:** generate a VP9 DASH ladder for MSE playback ([#11](https://github.com/Frikanalen/ingest/issues/11)) ([61a2207](https://github.com/Frikanalen/ingest/commit/61a22071afc60ac1a6b6d54e456608f1f9f9e93b))
* **ingest:** generate med_thumb and small_thumb alongside large_thumb ([#12](https://github.com/Frikanalen/ingest/issues/12)) ([52c676a](https://github.com/Frikanalen/ingest/commit/52c676a547a366b9d51ce8c111d690f744b03e90))
* **ingest:** measure R.128 loudness and normalize the DASH audio ([#16](https://github.com/Frikanalen/ingest/issues/16)) ([e912284](https://github.com/Frikanalen/ingest/commit/e912284ba686c1a042247016f8e50f48deda1da7))
* **ingest:** raise the DASH ladder to 5 / 2.5 / 0.6 Mb/s and return -cpu-used to 3 ([#17](https://github.com/Frikanalen/ingest/issues/17)) ([1a4144c](https://github.com/Frikanalen/ingest/commit/1a4144c417a671c5346b3a1b38d9f154f10cef41))
* **ingest:** remove the webm_med format now that DASH covers playback ([#14](https://github.com/Frikanalen/ingest/issues/14)) ([0720fde](https://github.com/Frikanalen/ingest/commit/0720fdec61ef5c14e6f4d208815573e5050a20ed))
* **ingest:** report pipeline state to django-api ([a7f8c60](https://github.com/Frikanalen/ingest/commit/a7f8c609e19b1269971e8c8b39fea7280561c116))
* **ingest:** track ffmpeg's own progress within a format ([#9](https://github.com/Frikanalen/ingest/issues/9)) ([26c86e6](https://github.com/Frikanalen/ingest/commit/26c86e6772d4b3bec3052213058ca367f55809ff))
* **ingest:** weight transcoding progress by format cost ([#10](https://github.com/Frikanalen/ingest/issues/10)) ([9690396](https://github.com/Frikanalen/ingest/commit/969039623e1e735e40ccbcc54039e2698ec0af7a))


### Bug Fixes

* adapt after testing in context with old API ([2f8c558](https://github.com/Frikanalen/ingest/commit/2f8c558906be39017eb5fd15bb787be9f8e2c6b7))
* adapt to new/old token endpoint ([8be4cd9](https://github.com/Frikanalen/ingest/commit/8be4cd9a76ce556e1e06e78b9a293eee219bf0d8))
* **chart:** authenticate to the API with a token, not a login ([fd5d0ff](https://github.com/Frikanalen/ingest/commit/fd5d0ffb26746619877f611e9a28c5e7cb0212c9))
* give the container's uid a name so SSH can connect ([#5](https://github.com/Frikanalen/ingest/issues/5)) ([cb1af44](https://github.com/Frikanalen/ingest/commit/cb1af44e257b196650cd7fc4b40fb785bf4ad894))
* **hooks:** take tusd's upload directory from settings ([7ea0f31](https://github.com/Frikanalen/ingest/commit/7ea0f312b00f56e384abce9d9a16c40d685e9054))
* **ingest:** align DASH segments to whole frames so seeking works ([#19](https://github.com/Frikanalen/ingest/issues/19)) ([0269f36](https://github.com/Frikanalen/ingest/commit/0269f3643ec758fbb0022c00acb3b8eb66b4f969))
* **typing:** correct type signature ([271a417](https://github.com/Frikanalen/ingest/commit/271a417149735852ea7bea49ea011faae3dc27bd))
* verify upload tokens before ingest ([#7](https://github.com/Frikanalen/ingest/issues/7)) ([42cbd53](https://github.com/Frikanalen/ingest/commit/42cbd53ad23a94696821a342ea6483135e964f7b))


### Performance Improvements

* **ingest:** fix thumbnail seeking, tune the DASH ladder, and give the pod resource requests ([#15](https://github.com/Frikanalen/ingest/issues/15)) ([2cabd17](https://github.com/Frikanalen/ingest/commit/2cabd17292fe46017e8decb0d22524bd0e5d3f2d))


### Dependencies

* get lib from github, not my home dir :) ([cc5b22c](https://github.com/Frikanalen/ingest/commit/cc5b22c388ce79973a949288c7bf97dc3c77ccef))


### Documentation

* improve README development guidance ([#3](https://github.com/Frikanalen/ingest/issues/3)) ([790be3a](https://github.com/Frikanalen/ingest/commit/790be3a4aeff70541999c8aa3cc13b972170e53d))
* **readme:** document the archive and how ingest is deployed ([8e4ebd6](https://github.com/Frikanalen/ingest/commit/8e4ebd60be5f041cb0531315a3b82d236616e48b))


### Code Refactoring

* **ingest:** follow django-api's format -&gt; variant rename ([#13](https://github.com/Frikanalen/ingest/issues/13)) ([40fdb44](https://github.com/Frikanalen/ingest/commit/40fdb445afc6db99e1a4cc303c1eb8b7b03c0bdb))
