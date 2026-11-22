;; Native structured terminal console & persistent side-panel state for Iter.
;; Mirrors upstream log.py's streaming event format, clip bounds, color policy,
;; and .iter-ui-pin.json atomic side-panel state without Python or curses.
!(import! &self (library fs))
!(import! &self (library json))
!(import! &self (library str))
!(import! &self (library system))

(= (iter-log:env-int $name $default)
   (let $raw (system:getenv-or-default $name "")
     (if (== $raw "")
         $default
         (let $parsed (str:string->number $raw)
           (if (== (get-type $parsed) Number)
               (floor-math $parsed)
               $default)))))

(= (iter-log:input-chars) (iter-log:env-int "ITER_INPUT_CHARS" 1200))
(= (iter-log:args-chars) (iter-log:env-int "ITER_ARGS_CHARS" 240))
(= (iter-log:result-chars) (iter-log:env-int "ITER_RESULT_CHARS" 260))
(= (iter-log:send-chars) (iter-log:env-int "ITER_SEND_CHARS" 2000))
(= (iter-log:pin-file) (system:getenv-or-default "ITER_PIN_FILE" ".iter-ui-pin.json"))
(= (iter-log:pin-max-chars) (let $n (iter-log:env-int "ITER_PIN_CHARS" 20000) (if (< $n 1000) 1000 $n)))

(= (iter-log:color-enabled)
   (and (== (system:getenv-or-default "NO_COLOR" "__unset__") "__unset__")
        (== (system:getenv-or-default "FORCE_COLOR" "0") "1")))

(= (iter-log:ansi-code $color)
   (case $color
     (("cyan" "36")
      ("blue" "34")
      ("green" "32")
      ("yellow" "33")
      ("red" "31")
      ("pink" "95")
      ("violet" "35")
      ($other "90"))))

(= (iter-log:esc) (str:from-codepoints (27)))

(= (iter-log:style $text $color)
   (if (iter-log:color-enabled)
       (str:join "" ((iter-log:esc) "[" (iter-log:ansi-code $color) "m" $text (iter-log:esc) "[0m"))
       $text))

(= (iter-log:time-label)
   (system:format-time "%H:%M:%S" (system:wall-time) "local"))

(= (iter-log:timestamp)
   (system:format-time "%Y-%m-%d %H:%M:%S" (system:wall-time) "local"))

(= (iter-log:clip $text $max)
   (let $len (str:char-length $text)
     (if (<= $len $max)
         $text
         (if (<= $max 3)
             (str:char-slice $text 0 $max)
             (str:concat (str:char-slice $text 0 (- $max 3)) "...")))))

(= (iter-log:pad-label $label)
   (let $len (str:char-length $label)
     (if (>= $len 9)
         $label
         (iter-log:pad-label (str:concat $label " ")))))

(= (iter-log:emit $label $message $color $detail)
   (let* (($leader (if $detail "            " (str:concat (iter-log:time-label) "  ")))
          ($line (str:join "" ($leader (iter-log:pad-label $label) " " $message))))
     (system:write stdout (str:concat (iter-log:style $line $color) "\n"))))

(= (iter-log:header $model $session $history_count)
   (let $short (if (> (str:char-length $session) 8) (str:char-slice $session 0 8) $session)
     (progn
       (iter-log:emit "ITER"
         (str:join "" ($model " · session " $short " · " (format-args "{}" ($history_count)) " messages"))
         "cyan" false)
       (iter-log:emit "NOTICE" "Classic streaming mode (no fixed input bar)" "yellow" false))))

(= (iter-log:incoming $message)
   (iter-log:emit "INPUT" (iter-log:clip $message (iter-log:input-chars)) "cyan" false))

(= (iter-log:status $message)
   (iter-log:emit "STATUS" (iter-log:clip $message (iter-log:input-chars)) "muted" false))

(= (iter-log:warning $label $message)
   (iter-log:emit $label (iter-log:clip $message (iter-log:input-chars)) "yellow" false))

(= (iter-log:error $label $message)
   (iter-log:emit $label (iter-log:clip $message (iter-log:result-chars)) "red" false))

(= (iter-log:failure $message)
   (iter-log:emit "FAILURE" (iter-log:clip $message (iter-log:result-chars)) "red" false))

(= (iter-log:retry $message)
   (iter-log:emit "RETRY" (iter-log:clip $message (iter-log:result-chars)) "yellow" false))

(= (iter-log:wait $seconds)
   (iter-log:emit "STANDBY"
     (str:join "" ("Checking for input for up to " (format-args "{}" ($seconds)) "s"))
     "muted" false))

(= (iter-log:llm-start)
   (iter-log:emit "INFER" "Generating tool calls" "violet" false))

(= (iter-log:usage-summary $usage_text)
   (if (or (== $usage_text "") (== $usage_text "None"))
       ""
       (case (json:parse $usage_text)
         (((JsonObject $pairs)
           (let* (($in (json:get (JsonObject $pairs) "prompt_tokens"))
                  ($out (json:get (JsonObject $pairs) "completion_tokens")))
             (if (and (== $in JsonNull) (== $out JsonNull))
                 ""
                 (str:join "" (" · tokens "
                               (if (== $in JsonNull) "?" (format-args "{}" ($in)))
                               "->"
                               (if (== $out JsonNull) "?" (format-args "{}" ($out))))))))
          ($other "")))))

(= (iter-log:llm-complete $duration $usage_text $call_count)
   (let* (($tenths (/ (round-math (* $duration 10)) 10))
          ($tokens (iter-log:usage-summary $usage_text)))
     (iter-log:emit "LLM"
       (str:join "" ((format-args "{}" ($tenths)) "s" $tokens " · " (format-args "{}" ($call_count)) " tool call(s)"))
       "blue" false)))

(= (iter-log:send-content $arguments)
   (case (json:parse-python $arguments)
     (((JsonObject $pairs)
       (let $content (json:get (JsonObject $pairs) "content")
         (if (== (get-type $content) String) $content "")))
      ($other ""))))

(= (iter-log:tool-call $name $arguments $ret $failed)
   (progn
     (if (== $name "send")
         (progn
           (iter-log:emit "SEND" $name "cyan" false)
           (let $msg (iter-log:send-content $arguments)
             (if (not (== $msg ""))
                 (iter-log:emit "MESSAGE" (iter-log:clip $msg (iter-log:send-chars)) "cyan" true)
                 ())))
         (progn
           (iter-log:emit "TOOL" $name "pink" false)
           (iter-log:emit "ARGS" (iter-log:clip $arguments (iter-log:args-chars)) "violet" true)))
     (if (and (not (== $ret "")) (not (== $ret "None")))
         (iter-log:emit (if $failed "ERROR" "RESULT")
           (iter-log:clip $ret (iter-log:result-chars))
           (if $failed "red" "green")
           true)
         (if $failed
             (iter-log:emit "ERROR" "Tool failed" "red" true)
             ()))))

(= (iter-log:stopped)
   (progn
     (iter-log:emit "OFFLINE" "Iter stopped" "muted" false)
     (system:write stdout "\nIter stopped.\n")))

;; Atomic persistent side-panel state matching upstream log.py's .iter-ui-pin.json schema.
(= (iter-log:clean-title $title)
   (let* (($t0 (str:join " " (str:split "\n" (str:join " " (str:split "\r" $title)))))
          ($t1 (str:trim $t0))
          ($t2 (if (> (str:char-length $t1) 64) (str:char-slice $t1 0 64) $t1)))
     (if (== $t2 "") "PINNED" $t2)))

(= (iter-log:pin $text $title)
   (let* (($target (iter-log:pin-file))
          ($max (iter-log:pin-max-chars))
          ($body (if (> (str:char-length $text) $max) (str:char-slice $text 0 $max) $text))
          ($clean_title (iter-log:clean-title $title))
          ($record (JsonObject ((JsonPair "title" $clean_title)
                                (JsonPair "text" $body)
                                (JsonPair "updated_at" (iter-log:timestamp)))))
          ($payload (json:stringify-python-with $record ((ensure-ascii False))))
          ($tmp (str:join "" ($target "." (system:uuid4-hex) ".tmp"))))
     (case (fs:write $tmp $payload)
       ((() (case (fs:rename $tmp $target)
              ((() true)
               ($err (progn (fs:remove $tmp) (Error "iter-log:pin" (format-args "{}" ($err))))))))
        ($err (Error "iter-log:pin" (format-args "{}" ($err))))))))

(= (iter-log:clear-pin) (iter-log:pin "" "PINNED"))

(= (iter-log:read-pin)
   (let $target (iter-log:pin-file)
     (if (fs:is-file $target)
         (case (json:parse (fs:read-text $target))
           (((JsonObject $pairs) (JsonObject $pairs))
            ($other JsonNull)))
         JsonNull)))
