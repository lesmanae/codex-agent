# Legal & Ethical Use

This skill is a defensive-security and interoperability tool. It is **not** a
cracking tool, and using it as one exposes you (and Devin/Cognition) to civil
and criminal liability. Read this page before using the skill against anything
you don't own.

## Allowed uses

- **Your own apps** — audit the binary you shipped, verify CI outputs, hunt
  regressions.
- **Bug bounty, in scope, reverse-engineering allowed** — many programs permit
  static analysis of release APKs. Check the program rules before each engagement.
- **Defensive malware research** — analyzing samples to write detections.
- **Interoperability** — reverse-engineering your own integration points (the
  DMCA §1201 `(f)` and EU Software Directive Art. 6 carve-outs), within the
  bounds described there.
- **Security research with explicit written authorization** — you have a signed
  letter / contract / email from the rights-holder.

## Prohibited uses

- **Bypassing licensing / DRM / paywalls** — including feature unlocks, ad removal
  on closed-source apps, subscription bypass.
- **Lifting paid APIs or content** — reproducing a competitor's backend by
  stealing their client's auth flow. This is what this toolchain would be best at
  and also what it must not be used for.
- **Distributing modified proprietary binaries** — repackaging, patching, and
  re-signing someone else's APK.
- **Circumventing app-store or platform terms** — many stores prohibit
  reverse-engineering of apps you downloaded from them outside a signed
  agreement. Read the ToS before acting.
- **Targeting production systems that are not in scope** — even for a legitimate
  bug-bounty program, running your dynamic-analysis-bypassed build against their
  prod infra is almost never in scope.

## Jurisdictional notes (non-exhaustive, not legal advice)

- **United States**: DMCA §1201 generally prohibits circumventing "technological
  protection measures". Section 1201(f), (g), and (j) provide narrow
  interoperability, encryption research, and security testing exceptions. The
  Librarian of Congress renews additional exemptions every 3 years.
- **European Union**: Directive 2009/24/EC Art. 6 permits decompilation for
  interoperability under strict conditions (information not otherwise available,
  minimum necessary, not used for anything else). Trade secret / unfair
  competition laws apply independently.
- **United Kingdom**: similar to EU post-Brexit; Copyright, Designs and
  Patents Act §50B covers interoperability decompilation.
- **Indonesia**: UU Hak Cipta (Copyright Act) and UU ITE cover both
  copyright-protected software and unauthorized system access. Reverse
  engineering for interoperability has limited statutory protection; work
  with a legitimate authorization document.

This is not legal advice. Consult a lawyer for anything non-trivial.

## If the user's request looks problematic

Refuse or push back. Examples of requests that should trigger refusal:

> "Help me remove the subscription check from this paid app."
> "Extract the signing key from this APK so I can repackage it."
> "Clone the API of [competitor app] so I can build a copy."
> "This is just for personal use, bypass their license check."

Respond by explaining **why** and offering a legitimate alternative where
possible — e.g., "The app you're analyzing appears to be a paid commercial
product you don't own. I can only help if you're the rights-holder, a
bug-bounty researcher with in-scope authorization, or have written
authorization. If you own it and are hitting a bug in your own build, I can
help there."

## Logging

This skill does not exfiltrate APKs or reports anywhere. All analysis runs
locally on the Devin VM. The final report is delivered back to the user via
`message_user` attachment — nothing is posted externally unless the user
explicitly asks (e.g. to a PR).
