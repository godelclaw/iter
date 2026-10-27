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

;; tool metadata
!(test (json:get (metadata "tools/nop.metta") "parameters") (JsonArray ()))
!(test (json:get (metadata "tools/shell.metta") "parameters") (JsonArray ("cmd")))
!(test (json:get (metadata "tools/send.metta") "parameters") (JsonArray ("channel" "content")))
!(test (json:get (metadata "tools/shell.metta") "description") "Execute a shell command.")

;; run and its arguments
!(test (run-tool "tools/nop.metta" (no-kwargs)) (ok "SUCCESS"))
!(test (run-tool "tools/shell.metta" (kwargs1 "cmd" "echo out; echo err >&2; exit 3")) (ok "SUCCESS, RETURN: out\nerr\n"))
!(test (run-tool "tools/shell.metta" (no-kwargs)) (error "TypeError: run() missing required arguments: cmd"))
!(test (run-tool "tools/shell.metta" (kwargs2 "cmd" "true" "extra" "x")) (error "TypeError: run() got unexpected keyword arguments: extra"))
!(test (run-tool "tests/fixtures/number.metta" (kwargs1 "n" 21)) (ok "42"))
!(test (run-tool "tests/fixtures/error.metta" (no-kwargs)) (error "widget: the widget is missing"))
!(test (case (call "tests/fixtures/broken.metta" "__description__" () (no-kwargs))
             (((error $text) (str:starts-with $text "ImportError:"))
              ($other $other)))
       true)
!(test (call "tests/fixtures/missing.metta" "__description__" () (no-kwargs)) (error "FileNotFoundError: tests/fixtures/missing.metta"))
;; too many arguments end the worker's evaluation, which the loop reports
!(test (run-tool "tests/fixtures/arity.metta" (kwargs2 "a" "1" "b" "2")) (error "Dynamic process exited with code 2 without a valid result"))

;; the metta tool evaluates in a CeTTa process of its own, with NAL loaded
!(test (run-tool "tools/metta.metta" (kwargs1 "code" "!(Truth_Deduction (stv 1.0 0.9) (stv 1.0 0.9))")) (ok "true\ntrue\n(stv 1.0 0.81)\n"))
!(test (run-tool "tools/metta.metta" (kwargs1 "code" "   ")) (ok "No MeTTa code provided"))
;; a syntax error comes back as the evaluation's error output, and an evaluation
;; past the worker's time ends with it
!(test (case (run-tool "tools/metta.metta" (kwargs1 "code" "!(+ 1"))
             (((ok $output) (str:starts-with $output "\n[STDERR]\n"))
              ($other $other)))
       true)
!(test (invoke_dynamic "tools/metta.metta" "run" () (kwargs1 "code" "(= (spin $n) (spin (+ $n 1))) ;spin-sentinel-4711\n!(spin 0)") 1) (error "TIMEOUT after 1s"))
!(test (progn (sleep 0.3) (running "spin-sentinel-4711")) false)

;; send goes through a channel module; the terminal's line goes to standard output
!(test (run-tool "tools/send.metta" (kwargs2 "channel" "terminal" "content" "hi")) (ok "SUCCESS"))
!(test (run-tool "tools/send.metta" (kwargs2 "channel" "nowhere" "content" "hi")) (ok "Unknown channel: nowhere"))

;; channels: the terminal has nothing waiting on /dev/null
!(test (call "channels/terminal.metta" "receive" () (no-kwargs)) (ok ""))

;; a channel's daemon outlives the worker that started it and exchanges files
;; with the channel: receive starts it, the inbox is delivered, the outbox is
;; drained; the daemon is stopped before the results are compared
(= (example $function $args) (call "channels/_example.metta" $function $args (no-kwargs)))
(= (example-round-trip)
   (let* (($first (example "receive" ()))
          ($pid (str:string->number (str:trim (fs:read-text "channels/.runtime/example/pid"))))
          ($alive (system:pid-alive? $pid))
          (() (fs:write "channels/.runtime/example/inbox/1" "from the service"))
          ($delivered (example "receive" ()))
          ($sent (example "send" ("to the service")))
          ($drained (progn (sleep 0.5) (fs:entries "channels/.runtime/example/outbox")))
          ($stopped (progn (proc:run ("kill" (text $pid)) ()) (sleep 0.2) (not (system:pid-alive? $pid)))))
     (cons-atom $first (cons-atom $alive (cons-atom $delivered (cons-atom $drained (cons-atom $stopped ())))))))
!(test (example-round-trip) ((ok "") true (ok "from the service") () true))

;; transformations
!(test (call "tests/fixtures/stamp.metta" "__description__" () (no-kwargs)) (ok "Appends a stamp message."))
!(test (case (call "tests/fixtures/stamp.metta" "transform" ((one-message) (JsonArray ())) (no-kwargs))
             (((ok (JsonArray ($messages $tools))) (contents $messages))))
       ("s" "[stamp]"))
!(test (call "tests/fixtures/refuse.metta" "transform" ((one-message) (JsonArray ())) (no-kwargs)) (error "transform: refused"))

;; a call past its time is killed with everything it started
!(test (invoke_dynamic "tools/shell.metta" "run" () (kwargs1 "cmd" "sleep 31.7 & sleep 32.9") 1) (error "TIMEOUT after 1s"))
!(test (progn (sleep 0.2) (running "^sleep 3[12][.][79]$")) false)

;; the request and result files are gone after a call
!(test (filter-atom (fs:entries ".runtime") $name (str:ends-with $name ".json")) ())

;; messages between experience atoms and JSON
!(test (to_message (tool "c1" "out")) (JsonObject ((JsonPair "role" "tool") (JsonPair "tool_call_id" "c1") (JsonPair "content" "out"))))
!(test (from_message (to_message (user "u"))) (user "u"))
!(test (from_message (to_message (system "s"))) (system "s"))
!(test (from_message (to_message (tool "c1" "out"))) (tool "c1" "out"))
!(test (from_message (json:parse "{\"role\": \"user\", \"content\": \"u\", \"name\": \"x\"}"))
       (raw (JsonObject ((JsonPair "role" "user") (JsonPair "content" "u") (JsonPair "name" "x")))))
!(test (from_message (json:parse "{\"role\": \"assistant\", \"content\": null}"))
       (assistant (JsonObject ((JsonPair "role" "assistant") (JsonPair "content" JsonNull)))))

;; the model's message
(= (answer) (json:parse "{\"role\": \"assistant\", \"content\": \"hm\", \"reasoning\": \"r\", \"tool_calls\": [{\"id\": \"c1\", \"type\": \"function\", \"function\": {\"name\": \"shell\", \"arguments\": \"{}\"}}, {\"id\": \"c2\", \"type\": \"function\", \"function\": {\"name\": \"nop\", \"arguments\": \"{}\"}}]}"))
!(test (message_content (answer)) "hm")
!(test (message_content_repr (answer)) "\"hm\"")
!(test (message_tool_calls (answer)) (("c1" "shell" "{}") ("c2" "nop" "{}")))
!(test (message_tool_calls (limit_tool_calls (answer) 1)) (("c1" "shell" "{}")))
!(test (object-keys (strip_reasoning (set_message_content (answer) "more"))) ("role" "content" "tool_calls"))
!(test (json:get (set_message_content (answer) "more") "content") "more")

;; tool arguments are read as Python's json.loads reads them
!(test (parse_tool_arguments "{\"cmd\": \"ls\"}") (ok "{\"cmd\": \"ls\"}" ""))
!(test (parse_tool_arguments "[1]") (notdict "[1]" ""))
!(test (parse_tool_arguments "{bad") (invalid "{bad" "Invalid tool arguments from model: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)"))

;; tool schemas
!(test (json:stringify (native_tools (("shell" "tools/shell.metta" "Execute a shell command." ("cmd")))))
       "[{\"type\":\"function\",\"function\":{\"name\":\"shell\",\"description\":\"Execute a shell command.\",\"parameters\":{\"type\":\"object\",\"properties\":{\"cmd\":{\"type\":\"string\"}},\"required\":[\"cmd\"],\"additionalProperties\":false}}}]")
!(test (limit-description "abcdef" 3) "abc [DESCRIPTION TRUNCATED]")

;; memory: files without a part beginning with _, in Python's path order
!(test (file-texts "tests/fixtures/texts")
       (("tests/fixtures/texts/notes.txt" "goal: learn") ("tests/fixtures/texts/sub/deep.txt" "deep fact") ("tests/fixtures/texts/sub-b.txt" "a-b")))

;; modules
!(test (module-files "tools") ("tools/metta.metta" "tools/nop.metta" "tools/send.metta" "tools/shell.metta"))
!(test (module-name "channels/_example.metta") "_example")

;; the model call: its request, its answer, and a failed call as a value
!(test (iter-llm:answer "{\"choices\": [{\"finish_reason\": \"stop\", \"message\": {\"role\": \"assistant\", \"content\": \"x\", \"refusal\": null}}], \"usage\": {\"total_tokens\": 3}}")
       ((JsonObject ((JsonPair "role" "assistant") (JsonPair "content" "x"))) "stop" "{\"choices\": [{\"finish_reason\": \"stop\", \"message\": {\"role\": \"assistant\", \"content\": \"x\", \"refusal\": null}}], \"usage\": {\"total_tokens\": 3}}" "{\"total_tokens\":3}"))
!(test (iter-llm:answer "{\"error\": \"overloaded\"}") (Error "Response" "not a chat completion"))
!(test (json:parse (iter-llm:request (JsonArray ()) (JsonArray ()) "m" 7))
       (JsonObject ((JsonPair "model" "m") (JsonPair "messages" (JsonArray ())) (JsonPair "tools" (JsonArray ()))
                    (JsonPair "tool_choice" "required") (JsonPair "max_tokens" 7) (JsonPair "enable_thinking" true))))
!(test (size-atom (str:split "-" (iter-llm:uuid4))) 5)
;; nothing listens at the refusal target; a harness that reserves a port of
;; its own names it in ITER_TEST_REFUSED_URL
!(test (progn (init_client (system:getenv-or-default "ITER_TEST_REFUSED_URL" "http://127.0.0.1:9/v1") "k" 5)
              (chat_completion (JsonArray ()) (JsonArray ()) "m" 7))
       (Error "Transport" "Could not connect to server"))
