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
