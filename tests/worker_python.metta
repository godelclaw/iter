;; Optional Python guest checks; requires a BUILD=python CeTTa.
;; The MeTTa worker through utils.metta's invoke_dynamic, and the helpers the
;; loop builds on. Run from the repository root:
;;   cetta --lang petta tests/worker.metta < /dev/null
!(import! &self "../utils.metta")

(= (call $path $function $args $kwargs) (invoke_dynamic $path $function $args $kwargs 5))
(= (run-tool $path $kwargs) (call $path "run" () $kwargs))
(= (metadata $path) (case (call $path "__tool_metadata__" () (no-kwargs)) (((ok $metadata) $metadata))))
(= (kwargs1 $key $value) (JsonObject ((JsonPair $key $value))))
(= (kwargs2 $k1 $v1 $k2 $v2) (JsonObject ((JsonPair $k1 $v1) (JsonPair $k2 $v2))))
(= (one-message) (JsonArray ((JsonObject ((JsonPair "role" "system") (JsonPair "content" "s"))))))
(= (contents (JsonArray $messages)) (map-atom $messages $message (json:get $message "content")))
(= (running $pattern) (case (proc:run ("pgrep" "-f" $pattern) ()) (((proc:result (proc:exited 0) $cause $out $err $capture) true) ($none false))))

;; Python modules, in the format of Python Iter, run in the worker's Python
!(test (json:get (metadata "tools/_python.py") "parameters") (JsonArray ("code")))
!(test (run-tool "tools/_python.py" (kwargs1 "code" "print(6 * 7)")) (ok "42\n"))
!(test (run-tool "tools/_python.py" (kwargs1 "code" "print(shell('printf native-from-python'))"))
       (ok "SUCCESS, RETURN: native-from-python\n"))
!(test (run-tool "tools/_python.py" (kwargs1 "code" "print(nop())")) (ok "SUCCESS\n"))
!(test (run-tool "tools/_python.py" (kwargs1 "code" "os.chdir('/'); print(shell('printf still-native'))"))
       (ok "SUCCESS, RETURN: still-native\n"))
!(test (run-tool "tests/fixtures/pyerror.py" (no-kwargs)) (error "ValueError: the widget is missing"))
!(test (call "tests/fixtures/pychannel.py" "receive" () (no-kwargs)) (ok "from python"))
!(test (case (call "tests/fixtures/pystamp.py" "transform" ((one-message) (JsonArray ())) (no-kwargs))
             (((ok (JsonArray ($messages $tools))) (contents $messages))))
       ("s" "[py stamp]"))
;; the send tool reaches a Python channel; the channel's name keeps it out of
;; the loop's channels
!(test (let* ((() (fs:write "channels/_pytest_channel.py" (fs:read-text "tests/fixtures/pychannel.py")))
              ($sent (run-tool "tools/send.metta" (kwargs2 "channel" "_pytest_channel" "content" "to python")))
              ($seen (fs:read-text ".runtime/pychannel-sent"))
              ($cleaned (progn (fs:remove "channels/_pytest_channel.py") (fs:remove ".runtime/pychannel-sent"))))
         ($sent $seen))
       ((ok "SUCCESS") "to python"))

