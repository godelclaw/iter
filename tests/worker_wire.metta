;; Strict JSON boundaries retain number kinds, signed zero and field occurrences.
!(import! &self "../utils.metta")
!(test (worker-wire-json (json:parse "[-0.0,0.0,0,1.0]")) "[-0.0, 0.0, 0, 1.0]")
!(test (worker-wire-json (json:parse "{\"a\":-0.0,\"a\":1}")) "{\"a\": -0.0, \"a\": 1}")
!(test (is-string (worker-wire-json (json:parse-python "NaN"))) false)
!(test (is-string (worker-wire-json (json:parse-python "Infinity"))) false)
!(test (is-string (worker-wire-json not-json-data)) false)
