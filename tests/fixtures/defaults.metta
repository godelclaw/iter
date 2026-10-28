(= (description) "Optional argument fixture.")
(= (parameters) (a b c))
(= (defaults) (JsonObject ((JsonPair "b" "B") (JsonPair "c" JsonNull))))
(= (run $a $b $c) (worker-wire-json (JsonArray ($a $b $c))))
