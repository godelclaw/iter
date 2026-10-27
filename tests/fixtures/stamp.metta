(= (description) "Appends a stamp message.")
(= (transform (JsonArray $messages) $tools)
   ((JsonArray (append $messages ((JsonObject ((JsonPair "role" "user") (JsonPair "content" "[stamp]")))))) $tools))
