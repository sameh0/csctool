# Bug RCA: unverified reboot and CSC success

- Symptom: a reply of `+CFUN=1,1:NA` followed by `OK` could be followed by “Phone is rebooting into CSC …” even when no reboot occurred.
- Root cause: the CLI discarded sequence results and printed unconditional success. Reply display prioritized an `OK` substring over negative results, and reboot errors were exempted from aborts without a corresponding outcome report.
- Blast radius: every `change` invocation; a failed reboot request does not establish that the earlier configuration request failed.
- SSOT/boundary issue: protocol observations now belong to `ATResult` and `classify_reply`; the CLI presents those observations without inferring device state.
- Fix: distinguish negative replies, acknowledgement, inconclusive replies, failed writes and lost connections. Report the configuration acknowledgement separately, with manual reboot guidance and post-boot verification instructions. Do not add device writes or automatic retries.
- Guardrail: offline transcript, transport-failure and CLI tests in `tests/test_outcomes.py`; run `python3 -m unittest discover -s tests -v`. Tests replace serial and block subprocess/ADB access. CI can run the same dependency-free suite; adding a GitHub Actions workflow requires publishing credentials with workflow scope.
- Residual risk: fixed-delay serial reads still lack full framing/correlation. Neither acknowledgement nor disconnect verifies reboot, persistence or active CSC. Samsung's proprietary `NA` meaning is not assumed. No hardware validation was performed for this change.
