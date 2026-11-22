;; Iter's OpenAI-compatible model call in MeTTa, over CeTTa's native HTTP.
;; Replaces utils.py's init_client and chat_completion; str-cat and text are
;; utils.metta's. The request is the one utils.py sends, and a failed call
;; answers (Error what why) instead of raising.
!(import! &self (library io))
!(import! &self (library json))
!(import! &self (library str))
!(import! &self (library system))

;; str(uuid.uuid4()): the 32 hex digits grouped 8-4-4-4-12
(= (iter-llm:uuid4)
   (let $hex (system:uuid4-hex)
        (str:join "-" ((str:char-slice $hex 0 8) (str:char-slice $hex 8 12) (str:char-slice $hex 12 16)
                       (str:char-slice $hex 16 20) (str:char-slice $hex 20 32)))))

;; One session id per client, sent with the tenant on every request as
;; utils.py's default_headers did.
(= (init_client $base_url $api_key $timeout)
   (change-state! &iter_llm_client (client $base_url $api_key $timeout (iter-llm:uuid4))))

(= (iter-llm:session-id)
   (let (client $base $key $timeout $session) (get-state &iter_llm_client) $session))

;; Like model_dump(exclude_none=True): absent and null fields are the same.
(= (iter-llm:without-nulls (JsonObject $pairs))
   (JsonObject (filter-atom $pairs $pair (let (JsonPair $key $value) $pair (not (== $value JsonNull))))))

(= (iter-llm:request $messages $tools $model $max_tokens)
   (iter-llm:request-with-options $messages $tools $model "required" $max_tokens
     (system:getenv-or-default "ITER_ENABLE_THINKING" "")))

(= (iter-llm:request-with-thinking $messages $tools $model $max_tokens $setting)
   (iter-llm:request-with-options $messages $tools $model "required" $max_tokens $setting))

;; This is a provider extension, so an unset option omits it from the request.
(= (iter-llm:thinking $setting)
   (case $setting
     (("" ())
      ("true" ((JsonPair "enable_thinking" true)))
      ("false" ((JsonPair "enable_thinking" false)))
      ($other (Error "Configuration" "ITER_ENABLE_THINKING must be true, false, or unset")))))

(= (iter-llm:request-with-options $messages $tools $model $tool_choice $max_tokens $setting)
   (case (iter-llm:thinking $setting)
     (((Error $what $why) (Error $what $why))
      ($extension
       (json:stringify
         (JsonObject (append ((JsonPair "model" $model)
                              (JsonPair "messages" $messages)
                              (JsonPair "tools" $tools)
                              (JsonPair "tool_choice" $tool_choice)
                              (JsonPair "max_tokens" $max_tokens)) $extension)))))))

;; returns message finish_reason response usage, as utils.py did
(= (iter-llm:answer $text)
   (let $response (json:parse $text)
        (case (json:get $response "choices")
          (((JsonArray $choices)
            (if (== $choices ())
                (Error "Response" "no choices")
                (let* (($choice (car-atom $choices))
                       ($msg_raw (json:get $choice "message"))
                       ($finish (json:get $choice "finish_reason"))
                       ($usage (json:get $response "usage")))
                  (case $msg_raw
                    (((JsonObject $pairs)
                      ((iter-llm:without-nulls (JsonObject $pairs))
                       (if (is-string $finish) $finish "None")
                       $text
                       (if (== $usage JsonNull) "None" (json:stringify $usage))))
                     ($other (Error "Response" "empty message")))))))
           ($other (Error "Response" "not a chat completion"))))))

;; True if the response did not return a usable LLM message (matches upstream utils.py:is_refusal).
(= (iter-llm:is-refusal $message $finish_reason)
   (let* (($finish_lower (str:lower (if (is-string $finish_reason) $finish_reason "")))
          ($finish_refused (or (== $finish_lower "refusal") (== $finish_lower "content_filter")))
          ($refusal_field (json:get $message "refusal"))
          ($has_refusal (and (not (== $refusal_field JsonNull))
                             (and (not (== $refusal_field false))
                                  (not (== $refusal_field "")))))
          ($content (json:get $message "content"))
          ($has_content (and (not (== $content JsonNull))
                             (and (not (== $content false))
                                  (not (== $content "")))))
          ($calls (json:get $message "tool_calls"))
          ($has_calls (case $calls
                        (((JsonArray $items) (not (== $items ())))
                         ($other false)))))
     (or $finish_refused (or $has_refusal (not (or $has_content $has_calls))))))

;; The answer to a submitted request, waited for as long as the client's
;; timeout; a request that times out is cancelled.
(= (iter-llm:outcome $id $timeout)
   (case (io:wait $id (* $timeout 1000))
     (((io:event $received (http:response 200 $text)) (iter-llm:answer $text))
      ((io:event $received (http:response $status $text)) (Error (str-cat ("HTTP " $status)) $text))
      ((io:event $received (Error $request (http:error $kind $code $message))) (Error "Transport" $message))
      ((io:idle) (progn (io:cancel $id) (Error "Timeout" (str-cat ("no answer within " $timeout " s")))))
      ($other (Error "Transport" (text $other))))))

(= (iter-llm:call-once $messages $tools $model $tool_choice $max_tokens)
   (case (iter-llm:request-with-options $messages $tools $model $tool_choice $max_tokens
           (system:getenv-or-default "ITER_ENABLE_THINKING" ""))
     (((Error $what $why) (Error $what $why))
      ($body (iter-llm:submit-body $body)))))

(= (chat_completion $messages $tools $model $max_tokens)
   (iter-llm:call-once $messages $tools $model "required" $max_tokens))

;; Primary first; if LLM_BACKUP_MODEL is set and distinct, retries once on
;; primary refusal or error (preserving the primary error if backup also fails).
;; Returns ($used_model $message $finish_reason $response $usage) or (Error what why).
(= (chat_completion_with_backup $messages $tools $model $backup_model $tool_choice $max_tokens)
   (let $use_backup (and (not (== $backup_model "")) (not (== $backup_model $model)))
     (case (iter-llm:call-once $messages $tools $model $tool_choice $max_tokens)
       (((Error $what $why)
         (if (not $use_backup)
             (Error $what $why)
             (case (iter-llm:call-once $messages $tools $backup_model $tool_choice $max_tokens)
               (((Error $bw $by) (Error $what $why))
                (($m2 $f2 $r2 $u2) ($backup_model $m2 $f2 $r2 $u2))))))
        (($m1 $f1 $r1 $u1)
         (if (or (not $use_backup) (not (iter-llm:is-refusal $m1 $f1)))
             ($model $m1 $f1 $r1 $u1)
             (case (iter-llm:call-once $messages $tools $backup_model $tool_choice $max_tokens)
               (((Error $bw $by) (Error $bw $by))
                (($m2 $f2 $r2 $u2) ($backup_model $m2 $f2 $r2 $u2))))))))))

(= (iter-llm:submit-body $body)
   (let* (((client $base $key $timeout $session) (get-state &iter_llm_client))
          ($url (str:concat $base "/chat/completions"))
          ($authorization (str:concat "Bearer " $key))
          ($milliseconds (* $timeout 1000))
          ;; http:request is data: bind the complete request before submitting it.
          ($headers ((http:header "Content-Type" "application/json")
                     (http:header "Authorization" $authorization)
                     (http:header "X-APC-Tenant" "iter")
                     (http:header "x-session-id" $session)))
          ($request (http:request "POST" $url $headers $body $milliseconds 67108864)))
     (case (io:submit $request)
       (((io:pending $id) (iter-llm:outcome $id $timeout))
        ((Error $what $why) (Error "Request" $why))))))
