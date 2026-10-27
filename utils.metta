;; utils.metta: the helpers of Iter's utils.py ported to MeTTa (PeTTa), on
;; CeTTa's native libraries. All configuration lives in iter.metta and is
;; passed in as function arguments.
;; The model call is iter_llm.metta (native HTTP), and tools, channels and
;; transformations are MeTTa modules run by iter_worker.metta, one CeTTa
;; process per call.
;; Experience messages are atoms: (user "c") (system "c") (tool "call_id" "c") (assistant <json>) (raw <json>)
;; JSON values are CeTTa's: (JsonObject ((JsonPair "key" value) ...)) keeps its key order, (JsonArray (...)),
;; JsonNull, strings, numbers, true and false.

!(import! &self (library json))
!(import! &self (library str))
!(import! &self (library fs))
!(import! &self (library system))
!(import! &self (library proc))
!(import! &self (library io))
!(import! &self iter_llm)

;; --------------------------------------------------------------------
;; Small helpers
;; --------------------------------------------------------------------
;; str() of Python: a string itself, any other atom as MeTTa writes it
(= (text $atom) (format-args "{}" (cons-atom $atom ())))
(= (str-cat $parts) (str:join "" (map-atom $parts $part (text $part))))
(= (is-string $atom) (== (get-type $atom) String))
(= (first-n $n $list) (if (or (<= $n 0) (== $list ())) () (cons-atom (car-atom $list) (first-n (- $n 1) (cdr-atom $list)))))

;; "what: why" of an (Error what why), as Python printed an exception
(= (error_text $error)
   (case $error
     (((Error $what $why) (str:concat (text $what) (str:concat ": " (text $why))))
      ($other (repr $other)))))

;; time.sleep: an io timer, waited for until it fires
(= (sleep $seconds)
   (let (io:pending $timer) (io:submit (timer:after (round-math (* $seconds 1000))))
        (timer-fired $timer)))
(= (timer-fired $timer)
   (case (io:wait $timer 1000)
     (((io:idle) (timer-fired $timer))
      ($fired true))))

;; JSON objects as Python's dicts: the keys, a key set in place or appended,
;; and keys removed
(= (object-keys (JsonObject $pairs)) (map-atom $pairs $pair (let (JsonPair $key $value) $pair $key)))
(= (object-put (JsonObject $pairs) $key $value)
   (JsonObject (if (is-member $key (object-keys (JsonObject $pairs)))
                   (map-atom $pairs $pair (let (JsonPair $name $old) $pair (if (== $name $key) (JsonPair $key $value) $pair)))
                   (append $pairs ((JsonPair $key $value))))))
(= (object-without (JsonObject $pairs) $keys)
   (JsonObject (filter-atom $pairs $pair (let (JsonPair $name $value) $pair (not (is-member $name $keys))))))

;; Path(path).stem of a module: its file name without .metta or .py
(= (module-name $path)
   (let* (($parts (str:split "/" $path))
          ($name (index-atom $parts (- (size-atom $parts) 1))))
     (if (str:ends-with $name ".metta") (str:char-slice $name 0 (- (str:char-length $name) 6))
         (if (str:ends-with $name ".py") (str:char-slice $name 0 (- (str:char-length $name) 3)) $name))))

(= (is-directory $path)
   (case (fs:metadata $path follow)
     (((fs:info directory $size $identity) true)
      ($other false))))

;; sorted .metta and .py modules of a directory, without names beginning with _
(= (module-files $dir)
   (if (is-directory $dir)
       (map-atom (sort-atom (filter-atom (fs:entries $dir) $name
                                         (and (or (str:ends-with $name ".metta") (str:ends-with $name ".py"))
                                              (not (str:starts-with $name "_")))))
                 $name (str-cat ($dir "/" $name)))
       ()))

;; --------------------------------------------------------------------
;; 1. Dynamic execution (cetta iter_worker.metta REQUEST_FILE):
;; --------------------------------------------------------------------
;; the running CeTTa, to start workers and nested evaluations with
(= (cetta) (system:executable))

;; a fresh file in the loop's private directory .runtime/
(= (runtime-file $suffix)
   (let () (fs:make-directories ".runtime" 448)
        (str-cat ((fs:canonical-path ".runtime") "/" (system:uuid4-hex) $suffix))))

;; A worker's output is discarded, and a timeout kills its process group. The
;; terminal is the loop's: its receive reads the loop's standard input, from
;; the loop's process group so that it can read a terminal, and a call that
;; sends to the terminal writes to the loop's standard output.
(= (worker-options $path $function $kwargs $timeout)
   (cons-atom (timeout-ms (round-math (* $timeout 1000)))
              (if (and (== (module-name $path) "terminal") (== $function "receive"))
                  ((stdin inherit) (group caller) (stdout null) (stderr null))
                  (if (== (json:get $kwargs "channel") "terminal")
                      ((stdout inherit) (stderr null))
                      ((stdout null) (stderr null))))))

;; Popen.returncode: the exit status, or the negated signal
(= (exit-code $termination)
   (case $termination
     (((proc:exited $code) $code)
      ((proc:signaled $signal) (- 0 $signal))
      ($other (repr $other)))))

;; the worker's result file: {"ok": true, "result": R} is (ok R), {"ok": false, "error": E} is (error E)
(= (worker-answer $finished $result_file $timeout)
   (case $finished
     (((proc:result (proc:launch-failed $errno $stage) $cause $out $err $capture)
       (error (str-cat ("cannot start the worker (" $stage ", errno " $errno ")"))))
      ((proc:result $termination proc:timed-out $out $err $capture)
       (error (str-cat ("TIMEOUT after " $timeout "s"))))
      ((proc:result $termination $cause $out $err $capture)
       (let $output (json:parse (fs:read-text $result_file))
            (case $output
              (((JsonObject $pairs) (if (== (json:get $output "ok") true)
                                        (ok (json:get $output "result"))
                                        (error (json:get $output "error"))))
               ($invalid (error (str-cat ("Dynamic process exited with code " (exit-code $termination) " without a valid result"))))))))
      ((proc:failed $kind $reason)
       (error (str-cat ("cannot start the worker: " $reason)))))))

(= (run-worker $path $function $args $kwargs $request_file $result_file $timeout)
   (let $request (json:stringify (JsonObject ((JsonPair "module" $path) (JsonPair "function" $function)
                                              (JsonPair "args" (JsonArray $args)) (JsonPair "kwargs" $kwargs)
                                              (JsonPair "result" $result_file))))
        (case (fs:write $request_file $request)
          ((() (worker-answer (proc:run ((cetta) "--lang" "petta" "--no-results" (fs:canonical-path "iter_worker.metta") $request_file)
                                        (worker-options $path $function $kwargs $timeout))
                              $result_file $timeout))
           ($failure (error (str-cat ("cannot write the worker request: " $failure))))))))

;; returns (ok result) or (error "text")
(= (invoke_dynamic $path $function $args $kwargs $timeout)
   (let* (($request_file (runtime-file ".json"))
          ($result_file (runtime-file ".json"))
          ($outcome (run-worker $path $function $args $kwargs $request_file $result_file $timeout)))
     (progn (fs:remove $request_file)
            (fs:remove $result_file)
            $outcome)))

(= (no-kwargs) (JsonObject ()))

;; --------------------------------------------------------------------
;; 2. Library for iter.metta
;; --------------------------------------------------------------------
(= (to_message $atom)
   (case $atom
     (((assistant $message) $message)
      ((raw $message) $message)
      ((tool $id $content) (JsonObject ((JsonPair "role" "tool") (JsonPair "tool_call_id" $id) (JsonPair "content" $content))))
      (($role $content) (JsonObject ((JsonPair "role" (str:symbol->string $role)) (JsonPair "content" $content)))))))

(= (keys-within $keys $allowed) (== (filter-atom $keys $key (not (is-member $key $allowed))) ()))
(= (json-str $value) (if (is-string $value) $value (json:stringify $value)))

(= (from_message $message)
   (let* (($role (json:get $message "role"))
          ($keys (object-keys $message)))
     (if (and (== $role "tool") (keys-within $keys ("role" "tool_call_id" "content")))
         (tool (json-str (json:get $message "tool_call_id")) (json-str (json:get $message "content")))
         (if (and (or (== $role "user") (== $role "system"))
                  (and (keys-within $keys ("role" "content")) (is-string (json:get $message "content"))))
             (cons-atom (str:string->symbol $role) (cons-atom (json:get $message "content") ()))
             (if (== $role "assistant")
                 (assistant $message)
                 (raw $message))))))

;; the saved experience, read as json.load reads it, or the (Error what why)
;; of a file that holds no JSON array
(= (load_experience)
   (if (fs:is-file "experience.json")
       (case (json:parse-python (fs:read-text "experience.json"))
         (((JsonArray $messages) (map-atom $messages $message (from_message $message)))
          ($other (Error "experience.json" (if (python-json-error $other) (index-atom $other 2) "not a JSON array")))))
       ()))

;; json.dump(experience, file, ensure_ascii=False, indent=2) and os.replace;
;; a failed write leaves no result
(= (save_experience $experience)
   (let* ((() (fs:write "experience.tmp" (json:stringify-python-with (JsonArray (map-atom $experience $atom (to_message $atom)))
                                                                     ((ensure-ascii False) (indent 2)))))
          (() (fs:rename "experience.tmp" "experience.json")))
     true))

(= (read_prompt) (str:trim (fs:read-text "prompt.txt")))

;; the files below a directory as a walk finds them, without those with a
;; path part beginning with _; symbolic links to directories are not followed
(= (walk-files $walker)
   (case (fs:walk-next $walker no-follow ())
     (((fs:walk-entry $path $relative $name $kind $index)
       (if (and (fs:is-file $path) (not (is-member true (map-atom (str:split "/" $relative) $part (str:starts-with $part "_")))))
           (cons-atom $path (walk-files $walker))
           (walk-files $walker)))
      ($end ()))))

;; returns (("dir/path" "content") ...), sorted as Python sorts paths, by their parts
(= (file-texts $directory)
   (map-atom (sort-atom (map-atom (walk-files (fs:walk-open $directory)) $file ((str:split "/" $file) $file)))
             $entry (let ($parts $path) $entry ($path (str:trim (fs:read-text $path))))))

(= (memory_files) (file-texts "memory"))

(= (channel_paths) (module-files "channels"))

(= (receive_channel $path $timeout)
   (case (invoke_dynamic $path "receive" () (no-kwargs) $timeout)
     (((ok $event) (if (or (== $event JsonNull) (== $event "")) "" (str-cat ("[" (module-name $path) "] " $event))))
      ((error $error) (str-cat ("[CHANNEL ERROR in " $path ": RuntimeError: " $error ". Repair " $path " if needed.]"))))))

(= (limit-description $description $max_description_chars)
   (if (> (str:char-length $description) $max_description_chars)
       (str:concat (str:char-slice $description 0 $max_description_chars) " [DESCRIPTION TRUNCATED]")
       $description))

(= (tool_metadata $path $max_description_chars $timeout)
   (case (invoke_dynamic $path "__tool_metadata__" () (no-kwargs) $timeout)
     (((ok $metadata) (inop (cons-atom (module-name $path)
                              (cons-atom $path
                                (cons-atom (limit-description (json:get $metadata "description") $max_description_chars)
                                  (cons-atom (json:array-items (json:get $metadata "parameters")) ()))))))
      ((error $error) (failed (str-cat ("[TOOL ERROR in " $path ": RuntimeError: " $error ". Repair " $path " if needed.]")))))))

;; returns ((name path description (parameters)) ...) omitted_tools errors
(= (load_tools $max_tools $max_description_chars $timeout)
   (let* (($paths (module-files "tools"))
          ($results (map-atom (first-n $max_tools $paths) $path (tool_metadata $path $max_description_chars $timeout))))
     ((map-atom (filter-atom $results $result (== (car-atom $result) inop)) $result (index-atom $result 1))
      (- (size-atom $paths) $max_tools)
      (str:join "\n" (map-atom (filter-atom $results $result (== (car-atom $result) failed)) $result (index-atom $result 1))))))

(= (native_tool ($name $path $description $parameters))
   (JsonObject ((JsonPair "type" "function")
                (JsonPair "function" (JsonObject ((JsonPair "name" $name)
                                                  (JsonPair "description" $description)
                                                  (JsonPair "parameters" (JsonObject ((JsonPair "type" "object")
                                                                                      (JsonPair "properties" (JsonObject (map-atom $parameters $parameter (JsonPair $parameter (JsonObject ((JsonPair "type" "string")))))))
                                                                                      (JsonPair "required" (JsonArray $parameters))
                                                                                      (JsonPair "additionalProperties" false))))))))))

;; returns the OpenAI tool schemas as a JSON array
(= (native_tools $inops) (JsonArray (map-atom $inops $inop (native_tool $inop))))

(= (load_transformation_descriptions $timeout)
   (str:join "\n" (map-atom (module-files "transformations") $path
                            (case (invoke_dynamic $path "__description__" () (no-kwargs) $timeout)
                              (((ok $description) (str-cat ((module-name $path) ": " $description)))
                               ($error (str-cat ((module-name $path) ": [DESCRIPTION MISSING]"))))))))

(= (transformation_step ($messages $tools $errors) $path $timeout)
   (case (invoke_dynamic $path "transform" (cons-atom $messages (cons-atom $tools ())) (no-kwargs) $timeout)
     (((ok (JsonArray ($new_messages $new_tools))) (cons-atom $new_messages (cons-atom $new_tools (cons-atom $errors ()))))
      ((ok $other) (cons-atom $messages (cons-atom $tools (cons-atom (append $errors (cons-atom (str-cat ("[RUNTIME ERROR in " $path ": ValueError: transform must return (messages tools). Repair " $path " if needed.]")) ())) ()))))
      ((error $error) (cons-atom $messages (cons-atom $tools (cons-atom (append $errors (cons-atom (str-cat ("[RUNTIME ERROR in " $path ": RuntimeError: " $error ". Repair " $path " if needed.]")) ())) ())))))))

;; messages as atoms, tools as a JSON array
(= (apply_transformation $messages $tools $timeout)
   (let ($new_messages $new_tools $errors) (foldl-atom (module-files "transformations")
                                                       ((JsonArray (map-atom $messages $atom (to_message $atom))) $tools ())
                                                       $state $path (transformation_step $state $path $timeout))
        (cons-atom (map-atom (json:array-items $new_messages) $message (from_message $message))
                   (cons-atom $new_tools (cons-atom (str:join "\n" $errors) ())))))

;; returns message finish_reason response usage, or the (Error what why) of a failed call
(= (llm $messages $tools $model $max_tokens) (chat_completion (JsonArray (map-atom $messages $atom (to_message $atom))) $tools $model $max_tokens))

(= (message_content $message)
   (let $content (json:get $message "content")
        (if (and (is-string $content) (not (== $content ""))) $content "")))

(= (message_content_repr $message)
   (let $content (json:get $message "content")
        (if (== $content JsonNull) "None" (repr $content))))

(= (set_message_content $message $content) (object-put $message "content" $content))

(= (message_calls $message) (let $calls (json:get $message "tool_calls") (if (== $calls JsonNull) () (json:array-items $calls))))

;; returns ((id name arguments) ...)
(= (message_tool_calls $message)
   (map-atom (message_calls $message) $call
             (let $function (json:get $call "function")
                  (cons-atom (json:get $call "id") (cons-atom (json:get $function "name") (cons-atom (json:get $function "arguments") ()))))))

(= (limit_tool_calls $message $limit)
   (if (== (message_calls $message) ())
       $message
       (object-put $message "tool_calls" (JsonArray (first-n $limit (message_calls $message))))))

(= (strip_reasoning $message) (object-without $message ("reasoning" "reasoning_details" "reasoning_content")))

;; json.loads of the arguments, as Python reads them; returns status(ok/notdict/invalid) printable_arguments error
(= (parse_tool_arguments $arguments)
   (case (json:parse-python $arguments)
     (((JsonObject $pairs) (ok $arguments ""))
      ($parsed (if (python-json-error $parsed)
                   (invalid $arguments (str-cat ("Invalid tool arguments from model: " (index-atom $parsed 2))))
                   (notdict $arguments ""))))))
(= (python-json-error $value) (if (== (get-metatype $value) Expression) (== (car-atom $value) JsonPythonErrorV1) false))

(= (invoke_tool $path $arguments $timeout)
   (case (invoke_dynamic $path "run" () (json:parse-python $arguments) $timeout)
     (((ok JsonNull) "None")
      ((ok $result) (text $result))
      ((error $error) (str-cat ("Tool execution failed: " $error))))))
