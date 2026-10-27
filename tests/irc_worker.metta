;; Loaded by the unchanged fresh JSON worker. Observe its original argument,
;; then use the base's explicit fresh-worker contract to call the REAL channel.
;; Retain that inner native JSON request/result, rather than importing a second
;; module's unqualified send into this module's namespace.
(= (irc-test:send $content)
   (let () (fs:write (system:getenv-or-default "IRC_TEST_OBSERVATION" "invalid")
               (json:stringify (JsonObject
                 ((JsonPair "content" $content)
                  (JsonPair "bytes" (str:byte-length $content))
                  (JsonPair "sha256" (str:sha256 $content))
                  (JsonPair "codepoints" (JsonArray (str:python-codepoints $content)))
                  (JsonPair "limits" (fs:read-text "/proc/self/limits"))))))
     (case (run-worker "channels/irc.metta" "send" (cons-atom $content ()) (no-kwargs)
                       (system:getenv-or-default "IRC_TEST_REQUEST" "invalid")
                       (system:getenv-or-default "IRC_TEST_RESULT" "invalid") 5)
       (((error $why) (Error IRC-boundary $why))
        ((ok $value) $value)))))
