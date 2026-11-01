# Translation catalogs

English is the source language and default. `tr.json` contains the Turkish UI.
German, French, Japanese, Korean, Portuguese (Portugal), and Simplified Chinese
catalogs include app labels and per-setting help. They were machine-assisted and
need native-speaker review. Technical logs and some dynamic errors remain English.
Create `<BCP47-code>.json` using the same `name` / `messages` structure to add a
language. The selector discovers catalogs automatically; missing messages fall
back to English. Add a `help` object keyed by the field names in `helptext.py`;
missing descriptions fall back to English. No network or account is needed at runtime.

Translate source strings, never setting keys, model names, paths or codec IDs.
Review translations in the UI before advertising a language as complete. This
development version does not claim to support every language.
