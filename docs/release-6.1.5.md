# 6.1.5 — officially published

Source-only recovery was verified in an isolated local clone: 23 paths restored exactly to baseline 138badaedfba76135646fcdb0968f718f3c304b5, with embedded version 6.1.4. Actual development checkout was unchanged. Prefer the following source-only restoration over blindly reverting the candidate commit, because later release-document changes can conflict:

```powershell
Set-Location D:/SoftWare-Work/CodexProject/dy-live-main/build/release-6.1.4/git-checkout
$taskRollbackPaths = @(git diff --name-only 138badaedfba76135646fcdb0968f718f3c304b5 240d989e9703e678e25aea1f375d69badcd77aab -- . ':(exclude)docs/release-6.1.5.md')
git restore --source=138badaedfba76135646fcdb0968f718f3c304b5 --staged --worktree -- $taskRollbackPaths
git diff --cached --check
git commit -m 'Restore pre-6.1.5 source; update publication handled separately'
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

Use only from a clean trusted checkout after preserving any new changes. This retains release documentation and changes neither server indices nor installed clients. For manual client restoration, gracefully close that installation and extract the verified old ZIP into a new empty directory, retaining LOCALAPPDATA/DYLiveAssistant and the saved browser profile path. Never remove highest-sequence state; automatic repair of clients that saw 6.1.5/7 requires a higher-version/sequence publication.

Final state: public signed full/incremental manifests switched to 6.1.5 sequence 7; independent public download acceptance completed. The historical hold below has been superseded by the user's explicit request to publish and defer stronger detail identity association. No existing safety guards were removed. Unique-card/title detail association remains a known limitation; real platform orders/payments are not verified.

Build source is candidate commit 240d989e9703e678e25aea1f375d69badcd77aab. GUI and nine embedded application/update/announcement/batch modules were compared against current source bytecode, and current source matches the trusted Git checkout. Main/helper/full ZIP hashes below remain unchanged. Signed incremental 6.1.4-to-6.1.5 ZIP SHA256 aa4516beec680b35635b5e08cd69ca9c2dc470c724d87c438fa8452f636dae24, 18,186,938 bytes; full ZIP 68,756,789 bytes. Official server signing keys stayed on the server. Full and incremental private signed targets are identical.

Resumed verification:
- Explicit 132-test run initially had four subtest failures in one multi-account test (zero observed simulated requests); failure log targeted-final.log retained. Diagnostic rerun observed exactly one request for each wrong/missing evidence case, zero successful orders, preserved browser and no retry. Isolated module recheck: 5 PASS. Final full explicit related module recheck: 132 PASS in 88.197s, targeted-resume-recheck.log. The fixture uses a short 100ms pending timeout; initial failure was not reproduced. No assertion or production timeout was weakened.
- Eight AST parses PASS; embedded GUI bytecode, nine modules and all five EXE ICO frames verified. Frozen title, tabs, shared SKU placement/copy example, new defaults, saved strict-price choice, SKU save/restart and shared batch display were visually checked. No task was started. User pressed Escape during final desktop close; no further Computer Use inputs were issued. Frozen announcement entry opened, but its complete reload/offline/admin UI coverage is not claimed.
- Actual signed current public 6.1.4 ZIP was validated/extracted as baseline. Both real full and real incremental helper upgrades committed and new EXE acknowledged startup. Managed roots match candidate exactly; user preference, external browser profile sentinel and unknown install file preserved. Incremental signature/index/plan hash/assembly/full target inventory PASS; modified and extra files rejected. First incremental harness used protocol 1 and was rejected before ready; corrected harness protocol 2 PASS. Source tests cover cancellation/space/recovery; no separate frozen cancellation/fault injection run is claimed.
- Original Docker build dependency download timed out, without modifying production. Existing backup image dependency versions exactly match all eight pinned requirements; fresh source was layered on that verified image as dy-license:candidate-6.1.5-reuse. Five announcement tests PASS inside this isolated image with network disabled. Actual old database COPY migration, integrity, all existing row counts, public API and rejected anonymous writes PASS. No synthetic announcement was published live.
- Service deployed from the accepted image; public /health and /api/v1/announcements 200, anonymous announcement POST 403. Nginx -t PASS; existing active configuration already accepts the exact full/delta/plan paths, so no Nginx edit or reload was necessary. Main signed index remains 6.1.4 until final publication below is separately confirmed.

Fresh predeployment backups: /opt/dy-updates/release-backups/round-6.1.5/resume-predeploy.sqlite3 SHA256 3019e419880ffac068298b8b2aed60906571481bd3bf8587890728104d8d5001, resume-predeploy-source-config.tgz SHA256 593688f08e57b80d2a18765e768f10f1faf6165a0bbe617620f65d8400425cdf; update-tools-before.tgz and image dy-license:backup-6.1.5 retained. Old public 6.1.4 resources retained. Existing source-before.zip and before/after Git bundles remain recoverable.

Service rollback commands on the verified host, if required:
```sh
tar -xzf /opt/dy-updates/release-backups/round-6.1.5/resume-predeploy-source-config.tgz -C /opt/dy-license
docker tag dy-license:backup-6.1.5 dy-license:local
docker compose -f /opt/dy-license/license_server/compose.yaml up -d --no-build --force-recreate license-api
docker exec blog-frontend nginx -t
```
The migration is additive; retain live database by default to avoid losing later admin/license writes. Database restore requires stopping license-api, preserving current database and WAL/SHM in a new directory, restoring the validated snapshot with owner 10001:10001, then starting the matching old image. Never blindly overwrite a writing database. A publication-specific atomic signed-index rollback script will be recorded after the switch; source Git revert and public-index withdrawal do not downgrade already updated clients. Forward repair must use a higher version and sequence, with 6.1.6/8 available only if still above all intervening releases.


Release tag push verified: `v6.1.5` peels to release documentation/source snapshot commit `825645c43eaed7b7fc5134ad3836df6055b8312a`; remote main matched at that point. Package bytes still match the candidate source commit (only documentation differs). Final user-facing notes now describe the published changes and explicitly state the real-platform/detail-identity limitation. Full manifest was re-signed server-side and the unchanged incremental ZIP rebound to that final manifest; final signed plan/index and whole-directory checks passed.

Verified server index-withdrawal command (verification mode succeeded against preserved old signed manifest and ZIP):
```sh
cd /opt/dy-updates/tools
python3 /opt/dy-updates/incoming/6.1.5-release/rollback-index-server.py --verify-only
# Execute only when withdrawing this exact release is intended:
python3 /opt/dy-updates/incoming/6.1.5-release/rollback-index-server.py
```
The script holds the existing publication lock, verifies the old signature and package hash, preserves current indices uniquely, restores the matching old incremental index (or removes its active reference while preserving it), then atomically restores latest.json. It only accepts current 6.1.5/7 and leaves all public versioned ZIP/plan resources intact. It is not automatic client downgrade.


Local distribution alias: D:/SoftWare-Work/CodexProject/dy-live-main/dist/DYLiveAssistant6.1.5.zip is byte-identical to the signed full package. Existing running dist executable was not replaced; old 6.1.4 installation and package remain available.

Database restoration commands, ONLY after deciding that losing later writes is acceptable and stopping all other database writers:
```sh
docker compose -f /opt/dy-license/license_server/compose.yaml stop license-api
backup=/opt/dy-updates/release-backups/round-6.1.5/db-current-$(date -u +%Y%m%dT%H%M%SZ)
mkdir -m 700 "$backup"
cp -a /opt/dy-license/license_server/runtime/data/. "$backup/"
test ! -e /opt/dy-license/license_server/runtime/data/licenses.sqlite3-wal || mv /opt/dy-license/license_server/runtime/data/licenses.sqlite3-wal "$backup/active-wal"
test ! -e /opt/dy-license/license_server/runtime/data/licenses.sqlite3-shm || mv /opt/dy-license/license_server/runtime/data/licenses.sqlite3-shm "$backup/active-shm"
install -o 10001 -g 10001 -m 600 /opt/dy-updates/release-backups/round-6.1.5/resume-predeploy.sqlite3 /opt/dy-license/license_server/runtime/data/licenses.sqlite3
# Restore matching service source/image using the commands above, then:
docker compose -f /opt/dy-license/license_server/compose.yaml up -d --no-build license-api
```
The preserved snapshot was integrity-checked before deployment. Do not execute a database rewind solely to withdraw the update manifest.


## Final publication evidence

Only after remote Git confirmation and private acceptance, the existing locked publisher uploaded immutable full/delta/plan resources, downloaded each through public HTTPS and verified SHA256 before switching incremental.json and finally latest.json. Public signed latest SHA256 58cc7af9729035ec307932ed5c9b0531bfc5e8b2715a0fa958940eb3e7e3ef8c; incremental index SHA256 6cf8e282a978294a8f5d429e00281f908d802cf45ebaed858af09bbb00658803; signed plan SHA256 7f283adc2eca543348a4a909c9b9506b1df8434695629e08b6ace08823140a97. Complete pre-switch old stable indices retained at /opt/dy-updates/release-backups/round-6.1.5/resume-stable.

Independent Windows-side PUBLIC downloads PASS: full 68,756,789 bytes / 351.25s, incremental 18,186,938 bytes / 78.44s; respective hashes a8d8325ff726dee83e74479e643a9aa38f84594b2b47d8788ab1cf041292e408 and aa4516beec680b35635b5e08cd69ca9c2dc470c724d87c438fa8452f636dae24. Both downloaded archives were checked against their signed file inventories. Public signed indices/plan match final private acceptance. UpdateClient public check offers 6.1.5/7 and its signed incremental plan to a 6.1.4 version, and no update to 6.1.5. Four unknown/sensitive resource paths returned 404. This is source client check plus actual EXE/helper private install evidence; not a separate old frozen GUI update-click test.

Announcement service remains healthy on accepted image sha256:7873bbc9bd9ecd03c41932c968f557d29d00ca43b0ea458fe4c75306d888b7b1; anonymous public announcement API remains 200 after manifest switch. No rollback was required. Old public 6.1.4 full ZIP and all unique prior backups retained. No real platform order/payment or full test discovery was run. Strong detail identity association, real platform behavior, full frozen announcement retry/offline UI/admin browser interaction and separate frozen cancellation/fault injection remain unverified. These limitations were not represented as passed; user explicitly deferred detail identity strengthening.

Evidence logs and generated manifests/downloads remain outside Git at D:/SoftWare-Work/CodexProject/dy-live-main/build/release-6.1.5. Executables/ZIPs are ignored per repository convention. Trusted Git remote is https://github.com/Tyler-Xixi/dy-live. v6.1.5 is the immutable release snapshot at 825645c43eaed7b7fc5134ad3836df6055b8312a, containing identical runtime source to build source commit 240d989e9703e678e25aea1f375d69badcd77aab. Subsequent main commits only record verification and recovery. No force push and no private key/credential/profile/database/log was committed.

---

# Initial candidate hold — historical record

Current online signed state was freshly verified as 6.1.4 sequence 6, package SHA256 bc651ff4221a647c9f9098f438fad8d77c10f0d6804a6efbbff8326896096d06. No public/incoming 6.1.5 existed at inspection. Candidate metadata is 6.1.5 sequence 7; final v6.1.5 tag is reserved until all acceptance gates pass. This is not a formal release.

Fresh pre-change targeted run: 131 PASS, 81.312s. After rejecting same-name/different-number detail ambiguity and adding its regression, the same explicit module list returned 132 PASS, 86.735s. Eight AST parses PASS. Commands and full individual test names are in build/release-6.1.5/targeted.log, targeted-after-fix.log and ast-check.py. Nine number tests separately PASS (3.398s). No full discovery or real-platform purchase/payment ran. Historical 58-test failures belong to old removed auto_pay interfaces/defaults and missing pending fixtures; prior 6.1.4 follow-up migration and deployment are recorded in release-6.1.4.md, not treated as fresh acceptance.

Actual new safety fix: dy_grab_gui.py rejects detail when multiple visible number cards share the selected title; tests/test_number_target.py now asserts this refusal and uses a unique-title fixture for the unrelated exact-number case. Existing root changes (number guards, announcement server/UI, incremental fallback diagnostics/routes and tests) were reviewed and synchronized to the trusted existing checkout. No safety check was weakened.

Unresolved blocking evidence: DETAIL_SNAPSHOT_JS still associates a unique target number card with a detail panel through matching title, without a reliable platform product identifier/verified causal binding. The explicitly supplied saved HTML contains 13 numbered cards, zero div.iHAKgO8B detail actions, zero detail headings outside li and no quantity/order-comment/pending-order detail. Its first card has only class attributes, no href and only data-e2e descendant attributes. User supplied the same path again; it cannot establish detail identity. Same-title collision is now refused, but unique-title matching alone remains insufficient. No production deployment or update manifest switch is permitted until this is fixed and fully exercised with appropriate detail evidence.

Clean main/helper build and complete candidate packaging succeeded. The first preliminary candidate accidentally referenced an old helper through a copied temporary spec; it is retained ONLY as excluded build evidence, never packaged or uploaded. main.spec was corrected to the new helper path, followed by a new clean build into final-candidate. Its updater bytes equal freshly built helper-dist. Embedded version is 6.1.5; isolated authorized EXE starts, correct title and graceful exit PASS. Frozen purchase/announcement/restore scenarios, private signed full/incremental installation, announcement deployment/admin browser and public resource acceptance are NOT completed. Startup alone is not frozen feature acceptance.

Artifacts (all under D:/SoftWare-Work/CodexProject/dy-live-main/build/release-6.1.5):
- final-candidate/DYLiveAssistant/DYLiveAssistant.exe SHA256 0610deb862ae86cce4f14ca7ac34ffb4318216a6f03c25181abff278d3b7b1b9
- final-candidate/DYLiveAssistant/DYLiveUpdater.exe SHA256 185cda4579b828119f3528ce689d4638251f072f561f728a18255ba81d4dc20e
- packages/6.1.5/DYLiveAssistant-6.1.5-windows-x64.zip SHA256 a8d8325ff726dee83e74479e643a9aa38f84594b2b47d8788ab1cf041292e408
No incremental package was built or signed, and no candidate resources were uploaded. Old installed dist was not replaced.

Backup sources: local source-before.zip; trusted Git baseline bundle build/release-6.1.4/git-before-6.1.5.bundle; existing 6.1.4 installation/dist and all prior backups retained. Remote backup /opt/dy-updates/release-backups/round-6.1.5 contains stable/ (signed complete and incremental indices), 6.1.4/ public release resources, license-source-config.tgz (service source, Compose and Nginx config), and online SQLite API backup licenses.sqlite3, integrity_check ok, permissions 600. Database SHA256 7d019a5915bcab18d15ed0b36030afa6a8cc15b33e787f82ac1f678867e25e4e. Service-source/config tar SHA256 593688f08e57b80d2a18765e768f10f1faf6165a0bbe617620f65d8400425cdf. Existing image retained as dy-license:backup-6.1.5. Secrets never downloaded or printed. Server/credential source remained build/manual-update/server_ops.py with pinned host key; actual service is dy-license-license-api-1. Nginx baseline check passed. Production DB/schema, service and config were not changed; backups are not a deployment.

Recovery: no online rollback is required because online state was not mutated. Candidate Git source revert uses `git revert --no-edit v6.1.5-rc.1` in build/release-6.1.4/git-checkout, then normal push (no force). This returns the synchronized changes to the known prior Git commit; do not infer installed-client rollback. Inspect the preserved service source/config without overwriting production: `mkdir /opt/dy-updates/release-backups/round-6.1.5/recovery-review` then `tar -xzf /opt/dy-updates/release-backups/round-6.1.5/license-source-config.tgz -C /opt/dy-updates/release-backups/round-6.1.5/recovery-review`. If a later deployment fails, stop writes before SQLite restore; retain the current database and WAL/SHM, restore the validated snapshot with service stopped, restore matching old config/image and nginx -t before reload. Do not blindly rewind live license/admin data after new writes. Current production was untouched, so executing that restore now would needlessly lose intervening data.

Server update withdrawal for a later publication must use the existing publish_lock and atomic_json to restore the verified stable/latest.json, plus its corresponding incremental index, retaining current signed indices uniquely first. The exact old 6.1.4 ZIP remains public. Client manual recovery extracts that verified full ZIP into a NEW empty directory after graceful close and preserves LOCALAPPDATA/DYLiveAssistant and browser profile paths. Never reset update_state.json to bypass replay protection. Highest-sequence/version checks prevent automatic downgrade. If sequence 7 or 6.1.5 has been seen, recovery code must use a still higher version/sequence (6.1.6/8 only if no intervening release). Git/index restore does not downgrade clients.


Final Git verification: candidate source commit `240d989e9703e678e25aea1f375d69badcd77aab` was pushed atomically with annotated `v6.1.5-rc.1` to `https://github.com/Tyler-Xixi/dy-live`. `git ls-remote` confirms main and the peeled tag both resolve to that commit; trusted checkout was clean. No final `v6.1.5` tag was created. A final signed public-manifest fetch still returned 6.1.4 sequence 6 and the recorded old ZIP hash. Public 6.1.5 download/install acceptance cannot be reported because it was not published.

Reproduce the targeted checks from the development directory in PowerShell (the isolated dependencies already exist):
```powershell
$env:PYTHONPATH = "$PWD/build/review-test-deps;$PWD;$PWD/tests"
python -m unittest -v test_number_target test_detail_lock test_multi_account_browser test_lock_mode_ui test_batch_layout test_announcements test_announcement_ui test_update_incremental test_update_incremental_staging test_update_ui test_update_diagnostics test_update_transaction test_update_client test_update_nginx_routes test_update_portable_install
python build/release-6.1.5/ast-check.py
```
The 132-test post-fix result is the current simulated/source evidence. It does not validate the remaining identity requirement.

Concrete source rollback, only if reverting this candidate is intended:
```powershell
Set-Location D:/SoftWare-Work/CodexProject/dy-live-main/build/release-6.1.4/git-checkout
git revert --no-edit 240d989e9703e678e25aea1f375d69badcd77aab
git -c http.proxy=http://127.0.0.1:7890 push origin main
```
Documentation-only commits after this source commit do not change candidate bytes. Local baseline restore into a separate empty directory: `Expand-Archive -LiteralPath D:/SoftWare-Work/CodexProject/dy-live-main/build/release-6.1.5/source-before.zip -DestinationPath D:/SoftWare-Work/CodexProject/dy-live-main/build/release-6.1.5/source-recovery-review`. Do not extract over the working source or user profiles.
