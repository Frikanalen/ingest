# Changelog

## [1.0.0](https://github.com/Frikanalen/ingest/compare/v0.1.1...v1.0.0) (2026-09-05)


### ⚠ BREAKING CHANGES

* **archive:** the engine reads the archive off a mount, not over SFTP ([#45](https://github.com/Frikanalen/ingest/issues/45))
* **archive:** the engine stops writing to the archive ([#44](https://github.com/Frikanalen/ingest/issues/44))
* **scripts:** `scripts/scan-metadata.py` and `scripts/backfill.py` are gone. They are `fk archive refresh-metadata` and `fk archive backfill` in fk-cli.
* **archive-utils:** mutate the archive through named commands, not write access ([#39](https://github.com/Frikanalen/ingest/issues/39))
* **ingest:** an upload supersedes the video it lands on ([#36](https://github.com/Frikanalen/ingest/issues/36))
* **ingest:** uploads go on the queue
* **backfill:** the upload Deployment is renamed, which to Kubernetes is a delete and a create. It owns a ReadWriteOnce volume, so the replacement cannot attach until the old pod has released it. Delete the old Deployment and let it finish terminating before upgrading, rather than trusting Helm to order the two. In-progress uploads survive -- the volume is untouched and tus clients resume from their offset.

### Features

* **api:** publish what this deployment can build ([#42](https://github.com/Frikanalen/ingest/issues/42)) ([752d842](https://github.com/Frikanalen/ingest/commit/752d842ce8cdf0e06e970afd30756e6d4f8074d1)), closes [#41](https://github.com/Frikanalen/ingest/issues/41)
* **archive-utils:** mutate the archive through named commands, not write access ([#39](https://github.com/Frikanalen/ingest/issues/39)) ([11a31de](https://github.com/Frikanalen/ingest/commit/11a31deabd0294d77fc5affd0f9296802d2e8575))
* **archive-utils:** ship the sudoers rule with the package, not as an example ([#51](https://github.com/Frikanalen/ingest/issues/51)) ([7b03b3f](https://github.com/Frikanalen/ingest/commit/7b03b3fdab2644c57848d4392f900634ea58700e))
* **archive:** add constrained variant deletion ([#47](https://github.com/Frikanalen/ingest/issues/47)) ([d4a6b90](https://github.com/Frikanalen/ingest/commit/d4a6b908c0966c6c3cbfd393245e69bf743bfde4))
* **archive:** the engine reads the archive off a mount, not over SFTP ([#45](https://github.com/Frikanalen/ingest/issues/45)) ([488ef44](https://github.com/Frikanalen/ingest/commit/488ef447eabd88d4f1a5355a765c7dc38f44a640))
* **archive:** the engine stops writing to the archive ([#44](https://github.com/Frikanalen/ingest/issues/44)) ([5908058](https://github.com/Frikanalen/ingest/commit/5908058bf5783730675e7e5a44c5f376cc8b0d50))
* **backfill:** reconcile the archive against what a video should have ([#21](https://github.com/Frikanalen/ingest/issues/21)) ([f678480](https://github.com/Frikanalen/ingest/commit/f67848089cd2c2db6db68e952071c107c47c3aaf))
* **chart:** run a fourth worker for dev-kube-4 ([#55](https://github.com/Frikanalen/ingest/issues/55)) ([16483f7](https://github.com/Frikanalen/ingest/commit/16483f7112f0f011e0c8c6baca42a37c452439d4))
* **dash:** publish a watchable preview while the ladder encodes ([#48](https://github.com/Frikanalen/ingest/issues/48)) ([025a20d](https://github.com/Frikanalen/ingest/commit/025a20d60b26fc8bab81f16a1e5ad55143347f65))
* **ingest:** an upload supersedes the video it lands on ([#36](https://github.com/Frikanalen/ingest/issues/36)) ([47395f7](https://github.com/Frikanalen/ingest/commit/47395f738af9b1259c35cfcec83e151ea3f4bdd7))
* **ingest:** uploads go on the queue ([1daac5a](https://github.com/Frikanalen/ingest/commit/1daac5a8b00437eee9dbbf1a463f306da4173d80))
* **logging:** report ingest progress updates ([#54](https://github.com/Frikanalen/ingest/issues/54)) ([c547d7d](https://github.com/Frikanalen/ingest/commit/c547d7d7bfda51dcdcf8e4b37982026e4e09b35f))


### Bug Fixes

* **app:** keep the debug directory watcher out of production ([#37](https://github.com/Frikanalen/ingest/issues/37)) ([445eb0c](https://github.com/Frikanalen/ingest/commit/445eb0c5dfaba60c97c6a533e0746077cf6512f6))
* **archive:** quote a failure that spoke on stdout instead of reporting silence ([#50](https://github.com/Frikanalen/ingest/issues/50)) ([0b2eb0b](https://github.com/Frikanalen/ingest/commit/0b2eb0ba8f578463d8048bf8c483d341968bc979))
* **backfill:** apply over the whole catalogue queued nothing ([#30](https://github.com/Frikanalen/ingest/issues/30)) ([95b3491](https://github.com/Frikanalen/ingest/commit/95b3491b06187fc4a9f95609432dde9dcefeb1a2))
* **backfill:** gc no longer trashes videos that are still being ingested ([#31](https://github.com/Frikanalen/ingest/issues/31)) ([28d4da1](https://github.com/Frikanalen/ingest/commit/28d4da118d119a0812a3beeb2cbf2eca58f16ba7))
* **backfill:** stop re-planning a loudness that can never be recorded ([#35](https://github.com/Frikanalen/ingest/issues/35)) ([48969b0](https://github.com/Frikanalen/ingest/commit/48969b027da9f26eef7ee27f90f8f8a310a38e37))
* **client:** refresh the API snapshot so a PATCH response parses again ([#49](https://github.com/Frikanalen/ingest/issues/49)) ([4df24c2](https://github.com/Frikanalen/ingest/commit/4df24c25cab2ec6cba867419e0258c7ca2260837))
* **dash:** encode audio as AAC, and declare a segment length that exists ([#25](https://github.com/Frikanalen/ingest/issues/25)) ([f03df15](https://github.com/Frikanalen/ingest/commit/f03df154defa9ce06e1c0bdfc82ba35afb44ff79))
* **hooks:** raise the upload gates instead of asserting them ([#29](https://github.com/Frikanalen/ingest/issues/29)) ([d266219](https://github.com/Frikanalen/ingest/commit/d266219d0456b755b6e6da7025605e57ad9baab8))
* **logging:** stop the video-id filter accumulating on the logger ([#56](https://github.com/Frikanalen/ingest/issues/56)) ([65768ff](https://github.com/Frikanalen/ingest/commit/65768ffc9ac1de961c2e7954498961671d0332d0))
* programme images in the reconciler, and a spool that never emptied ([#38](https://github.com/Frikanalen/ingest/issues/38)) ([5bec7d0](https://github.com/Frikanalen/ingest/commit/5bec7d083067a5b8f4bb8a1f0f2a23125d012a2d))
* **worker:** do not lose a SIGTERM that lands during startup ([#57](https://github.com/Frikanalen/ingest/issues/57)) ([83747d5](https://github.com/Frikanalen/ingest/commit/83747d5459be8b9c0d06a2aca8601f215b80a58c))
* **workers:** enforce topology spread ([#53](https://github.com/Frikanalen/ingest/issues/53)) ([a9b71e7](https://github.com/Frikanalen/ingest/commit/a9b71e733442f5c82172a1ce75b2c6fe7286de77))
* **workers:** let a rollout replace a worker instead of deadlocking ([#52](https://github.com/Frikanalen/ingest/issues/52)) ([feb9c5e](https://github.com/Frikanalen/ingest/commit/feb9c5e741aaa5201deebf029f1b84d004d5db84))


### Performance Improvements

* **runner:** stop retaining ffmpeg's progress stream ([#32](https://github.com/Frikanalen/ingest/issues/32)) ([cd5d67f](https://github.com/Frikanalen/ingest/commit/cd5d67f9c9d9644b924693b59e546d71aeac75de))


### Code Refactoring

* **scripts:** the queue side leaves for fk-cli ([#43](https://github.com/Frikanalen/ingest/issues/43)) ([2409c4f](https://github.com/Frikanalen/ingest/commit/2409c4fcbe2e233edb805c440371c563f83dfa0c))

## [0.1.1](https://github.com/Frikanalen/ingest/compare/v0.1.0...v0.1.1) (2026-08-31)


### Bug Fixes

* **ci:** tag release images with the release-please version ([#23](https://github.com/Frikanalen/ingest/issues/23)) ([b01a719](https://github.com/Frikanalen/ingest/commit/b01a719d93752e078401c68a827ef61008c86589))

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
