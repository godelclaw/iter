;; The MeTTa worker through utils.metta's invoke_dynamic. Run from the
;; repository root: cetta --lang petta tests/worker.metta < /dev/null
!(import! &self "../utils.metta")

(= (call $path $function $args $kwargs) (invoke_dynamic $path $function $args $kwargs 5))
(= (run-tool $path $kwargs) (call $path "run" () $kwargs))
(= (metadata $path) (case (call $path "__tool_metadata__" () (no-kwargs)) (((ok $metadata) $metadata))))
(= (kwargs1 $key $value) (json-object (cons-atom (json-pair $key $value) ())))
(= (kwargs2 $k1 $v1 $k2 $v2) (json-object (cons-atom (json-pair $k1 $v1) (cons-atom (json-pair $k2 $v2) ()))))
(= (one-message) (cons-atom (json-object ((json-pair role "system") (json-pair content "s"))) ()))
(= (contents $messages) (map-atom $messages $message (json-get $message content "")))

;; tool metadata
!(test (json-get (metadata "tools/nop.metta") parameters missing) ())
!(test (json-get (metadata "tools/shell.metta") parameters missing) ("cmd"))
!(test (json-get (metadata "tools/send.metta") parameters missing) ("channel" "content"))
!(test (json-get (metadata "tools/shell.metta") description missing) "Execute a shell command.")

;; run and its arguments
!(test (run-tool "tools/nop.metta" (no-kwargs)) (ok "SUCCESS"))
!(test (run-tool "tools/shell.metta" (kwargs1 cmd "echo out; echo err >&2; exit 3")) (ok "SUCCESS, RETURN: out\nerr\n"))
!(test (run-tool "tools/shell.metta" (no-kwargs)) (error "TypeError: run() missing required arguments: cmd"))
!(test (run-tool "tools/shell.metta" (kwargs2 cmd "true" extra "x")) (error "TypeError: run() got unexpected keyword arguments: extra"))
!(test (run-tool "tests/fixtures/number.metta" (kwargs1 n 21)) (ok "42"))
!(test (run-tool "tests/fixtures/error.metta" (no-kwargs)) (error "widget: the widget is missing"))
!(test (case (call "tests/fixtures/broken.metta" "__description__" () (no-kwargs))
             (((error $text) (succeeds (collapse (progn (translatePredicate (sub_string $text 0 12 $after "ImportError:")) yes))))
              ($other $other)))
       true)

;; the metta tool evaluates in a CeTTa process of its own, with NAL loaded
!(test (run-tool "tools/metta.metta" (kwargs1 code "!(Truth_Deduction (stv 1.0 0.9) (stv 1.0 0.9))")) (ok "true\ntrue\n(stv 1.0 0.81)\n"))
!(test (run-tool "tools/metta.metta" (kwargs1 code "   ")) (ok "No MeTTa code provided"))

;; send goes through a channel module
!(test (run-tool "tools/send.metta" (kwargs2 channel "terminal" content "hi")) (ok "SUCCESS"))
!(test (run-tool "tools/send.metta" (kwargs2 channel "nowhere" content "hi")) (ok "Unknown channel: nowhere"))

;; channels: the terminal has nothing waiting on /dev/null
!(test (call "channels/terminal.metta" "receive" () (no-kwargs)) (ok ""))

;; transformations
!(test (call "tests/fixtures/stamp.metta" "__description__" () (no-kwargs)) (ok "Appends a stamp message."))
!(test (case (call "tests/fixtures/stamp.metta" "transform" (cons-atom (one-message) (cons-atom () ())) (no-kwargs))
             (((ok ($messages $tools)) (contents $messages))))
       ("s" "[stamp]"))
!(test (call "tests/fixtures/refuse.metta" "transform" (cons-atom (one-message) (cons-atom () ())) (no-kwargs)) (error "transform: refused"))

;; a call past its time is killed with everything it started
!(test (invoke_dynamic "tools/shell.metta" "run" () (kwargs1 cmd "sleep 30 & sleep 31") 1) (error "TIMEOUT after 1s"))
