(= (description) "Invalid defaults must not execute a tool body.")
(= (parameters) (a))
(= (defaults) (JsonObject ((JsonPair "undeclared" "value"))))
(= (run $a) (fs:write ".runtime/defaults-bad-ran" "bad"))
