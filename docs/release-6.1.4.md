# 6.1.4 candidate — publication held

2026-10-10: clean main EXE and independent updater built; embedded version 6.1.4, sequence 5. No production manifest switch. Server latest remains signed 6.1.3 sequence 4. Candidate tag is v6.1.4-rc.1; it is not a released production version.

Targeted final run: 112 tests passed (48.897s). Additional legacy purchase-path run: 58 tests, FAILED (3 failures, 33 errors, 15.002s), including removed auto_pay constructor/method interfaces, changed strict-match defaults, and card-to-pending fixtures without required evidence. These failures are unresolved; no claim that all purchase paths passed. Full suite and real-platform orders/payment were not run.

Frozen EXE: embedded version verified, valid existing offline authorization copied only into temporary data, main title and both visible tabs checked via screenshot; normal shutdown passed. Default/shared-spec/preference restore assertions passed in source Tk tests. Those assertions have not all been exercised interactively in the frozen EXE. Isolated real-helper 6.1.3 to 6.1.4 upgrade passed signature/archive checks, startup acknowledgement and all managed-root hashes; synthetic config/browser preservation check is recorded separately in local evidence. No real browser session or credentials were used in update testing.

Artifacts stay outside Git per existing build/dist ignore rules:
- build/release-6.1.4/candidate/DYLiveAssistant/
- build/release-6.1.4/packages/6.1.4/DYLiveAssistant-6.1.4-windows-x64.zip
- build/release-6.1.4/hashes.json
- build/release-6.1.4/source-before.zip (pre-build workspace snapshot)
- build/release-6.1.4/git-before.bundle (existing remote baseline)
- build/release-6.1.4/targeted-tests-verified.log and purchase-simulation.log

Main EXE SHA256: 1f02480ab05b71034154915d2ac3f6b64c4f0e9177b3c33bc65813c79023029f
Updater SHA256: 65dc3513c42c06590157a7cae967042d49f14c7a9c57aeebbe5a01d9ad47921f
ZIP SHA256: b9f37c3c8c8459f2406a5f88706acdbabfac89d4b93a0c189b2f861105af497f

Server: SSH endpoint and pinned host key were read from existing build/manual-update/server_ops.py. Credentials were read there without printing. Existing signer was used under /opt/dy-updates/tools; private key never downloaded. Incoming candidate is /opt/dy-updates/incoming/6.1.4; private signed candidate is /opt/dy-updates/acceptance-6.1.4. Candidate has not been exposed via public download.

Old server backup: /opt/dy-updates/release-backups/round-6.1.4/latest.json (includes signature), SHA256 516f7d458c22c7a9efc9cdd714c4df111e9e791e7b8798b7f50e62b81c949806.
Old ZIP in the same directory: DYLiveAssistant-6.1.3-windows-x64.zip, SHA256 90f19a0adbf406f5649d8735b8dae37e56f22c621f89c746fd1f324e4258f355. Original public old release retained; local old ZIP also verified and extracted into build/release-6.1.4/old-install.

## Rollback commands

Git (run in build/release-6.1.4/git-checkout; inspect first, no force push):
```powershell
git revert --no-edit v6.1.4-rc.1
git push origin main
```
This reverts the synchronized source to the previous Git baseline ef20ce44ba81e8ba2996054681981c81f05e446b. That baseline predates online 6.1.3; it must not be described as 6.1.3 source. To inspect the local pre-build source snapshot safely:
```powershell
python -m zipfile -e build/release-6.1.4/source-before.zip build/release-6.1.4/source-rollback-review
```

Server manifest rollback, only if a later switch was made and after checking the current index; run in the existing authenticated SSH session and serialize against publisher operations:
```bash
sha256sum /opt/dy-updates/release-backups/round-6.1.4/latest.json
cp -n /opt/dy-updates/public/stable/latest.json /opt/dy-updates/release-backups/round-6.1.4/latest-before-rollback.json
cp /opt/dy-updates/release-backups/round-6.1.4/latest.json /opt/dy-updates/public/stable/latest.rollback.pending
mv /opt/dy-updates/public/stable/latest.rollback.pending /opt/dy-updates/public/stable/latest.json
```
No rollback is needed now: production index was never switched. Preserve all versioned resources. If a later release uses incremental updates, its index also needs a corresponding baseline; this candidate contains no incremental package.

Client: normal update checks require a higher version, and highest_sequence is monotonic. Restoring the server index does not automatically downgrade installed clients; clients that saw sequence 5 may reject sequence 4. For manual recovery, gracefully close the application, verify the old ZIP hash above, extract the complete 6.1.3 ZIP into a NEW empty directory, and run its EXE. Retain the newer installation and %LOCALAPPDATA%/DYLiveAssistant, and preserve configured absolute browser-profile paths; do not overwrite data or delete update_state.json. Compatibility of new preferences with an old client requires separate validation. Preferred fleet repair: recover known-good old source, set a new version higher than every published/seen version (for example 6.1.5 if 6.1.4 remains the maximum), and sequence higher than every seen sequence (for example 6), rebuild and validate, then publish with the existing signer. Git rollback alone never rolls back installed clients.


## Follow-up verification — 2026-10-10

Publication remains held. No build, package, server mutation, real order/payment, or full-suite run was performed in this follow-up.

The historical 58-test failure was reproduced and migrated: removed auto_pay/payment-success interfaces, old strict-match/stock-continuation defaults, and incomplete pending-order fixtures were test incompatibilities. Meaningful SKU availability, missing/duplicate selections, price, quantity, timeout, stop, single-submit and confirmation assertions remain.

Actual source issues found: detail lock rejected the pre-selection price before selecting the requested SKU, and product-image overlays could intercept SKU clicks. dy_grab_gui.py now checks the final price after SKU selection with a bounded two-second hydration wait, and installs the existing image guard before detail processing. Identity, quantity, amount, button and pending-evidence checks remain in force.

Fresh targeted results: product options 45 PASS; purchase reliability 4 PASS; lock modes 9 PASS (58 total). Additional affected-path checks: detail lock 37 PASS; multi-account browser 5 PASS; lifecycle 10 PASS. Total 110 PASS. Evidence logs are under build/release-6.1.4/*-recheck.log; the original purchase-simulation.log is preserved as historical failure evidence.

Existing frozen candidate was launched with a separate temporary LOCALAPPDATA/APPDATA and inspected with Computer Use. Verified clean defaults (sold-out continuation/detail lock enabled; ordinary/script lock and shared method switches disabled), shared-spec heading and example, then enabled SKU selection and entered the three-group example. Verified preferences.json values, closed normally, restarted the same EXE with the same isolated data, and visually confirmed the enabled switch and exact example restored. No start-task/login/order controls were activated. Only existing authorization material was copied locally into isolated test data, without printing it.

IMPORTANT: the existing EXE/archive/hashes above predate these source fixes. Frozen UI verification applies to that candidate only; it does not establish that a rebuilt binary contains the fixes. Rebuild and relevant frozen regression verification are required before any later publication. No production-server state was independently rechecked in this follow-up.


## RC2 fresh release verification — 2026-10-10

Current candidate is version 6.1.4, sequence 6, tag v6.1.4-rc.2. Old rc1, sequence 5, is retained and must never be distributed. Latest source fixes and migrated tests were synchronized into the existing Git checkout before committing. A clean build uses an independent helper and new rc2 output/work directories; the complete embedded GUI bytecode equals the current source. Bundled PNG is pixel-identical to the supplied whale image and bundled ICO matches assets/app.ico.

Fresh targeted run: 141 PASS in 89.346s; build/release-6.1.4/rc2/targeted.log. Includes the 110 affected-path tests plus UI/update release/signature/transaction/helper checks. No full suite, real-platform order or payment performed.

New frozen EXE was opened with existing valid offline authorization copied only into temporary data. Title, tabs, defaults and shared-spec heading/example were inspected, SKU selection was enabled with the exact three-group example, preferences were saved and the same isolated instance restarted. No purchase task or browser-login control was activated. Real updater 6.1.3 to new signed 6.1.4 passed startup acknowledgement, all managed-root hashes and synthetic configuration/browser-directory preservation. Evidence: rc2/upgrade.log, upgrade-evidence/real-exe-update.json, ui-context.json, shared-spec.png and restored-spec.png.

Final artifact directory: build/release-6.1.4/rc2/candidate/DYLiveAssistant. Complete ZIP: build/release-6.1.4/rc2/packages/6.1.4/DYLiveAssistant-6.1.4-windows-x64.zip.
Main SHA256 fc71a029f31e26cb2e22c92b4b1629ff23bc9bef55013a25ce4d2b20bda76f8e
Updater SHA256 77426b35d9317ab7a099fc4e9f7ec7ac391090c167f51f25212d9e8d411d9aba
ZIP SHA256 bc651ff4221a647c9f9098f438fad8d77c10f0d6804a6efbbff8326896096d06 (68,748,282 bytes).

Old server backup rechecked: round-6.1.4/latest.json SHA256 516f7d458c22c7a9efc9cdd714c4df111e9e791e7b8798b7f50e62b81c949806; old ZIP SHA256 90f19a0adbf406f5649d8735b8dae37e56f22c621f89c746fd1f324e4258f355. Private rc2 incoming: /opt/dy-updates/incoming/6.1.4-rc2; signature validated with official public key from /opt/dy-updates/acceptance-6.1.4-rc2/stable/latest.json. All rc1 and old source/Git/online backups retained. Additional latest-source baseline: rc2/source-before-build.zip.

Git source rollback for only the follow-up changes: git revert --no-edit v6.1.4-rc.2; git push origin main. This returns to rc1 source, which lacks the two fixes; assess before distributing. For client repair use a version above 6.1.4 and sequence above 6 (for example 6.1.5/7 if no intervening release). Old manifest restoration still does not downgrade installed clients, and clients that saw sequence 6 may reject sequence 4.

Production switch and remote Git verification are pending at the time of this RC2 source commit. Final publication record will supersede this status.
