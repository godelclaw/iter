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

;; Like model_dump(exclude_none=True): absent and null fields are the same.
(= (iter-llm:without-nulls (JsonObject $pairs))
   (JsonObject (filter-atom $pairs $pair (let (JsonPair $key $value) $pair (not (== $value JsonNull))))))

(= (iter-llm:request $messages $tools $model $max_tokens)
   (json:stringify (JsonObject ((JsonPair "model" $model)
                                (JsonPair "messages" $messages)
                                (JsonPair "tools" $tools)
                                (JsonPair "tool_choice" "required")
                                (JsonPair "max_tokens" $max_tokens)
                                (JsonPair "enable_thinking" true)))))

;; returns message finish_reason response usage, as utils.py did
(= (iter-llm:answer $text)
   (let $response (json:parse $text)
        (case (json:get $response "choices")
          (((JsonArray $choices)
            (if (== $choices ())
                (Error "Response" "no choices")
                (let* (($choice (car-atom $choices))
                       ($finish (json:get $choice "finish_reason"))
                       ($usage (json:get $response "usage")))
                  ((iter-llm:without-nulls (json:get $choice "message"))
                   (if (is-string $finish) $finish "None")
                   $text
                   (if (== $usage JsonNull) "None" (json:stringify $usage))))))
           ($other (Error "Response" "not a chat completion"))))))

;; The answer to a submitted request, waited for as long as the client's
;; timeout; a request that times out is cancelled.
(= (iter-llm:outcome $id $timeout)
   (case (io:wait $id (* $timeout 1000))
     (((io:event $received (http:response 200 $text)) (iter-llm:answer $text))
      ((io:event $received (http:response $status $text)) (Error (str-cat ("HTTP " $status)) $text))
      ((io:event $received (Error $request (http:error $kind $code $message))) (Error "Transport" $message))
      ((io:idle) (progn (io:cancel $id) (Error "Timeout" (str-cat ("no answer within " $timeout " s")))))
      ($other (Error "Transport" (text $other))))))

(= (chat_completion $messages $tools $model $max_tokens)
   (let* (((client $base $key $timeout $session) (get-state &iter_llm_client))
          ($url (str:concat $base "/chat/completions"))
          ($authorization (str:concat "Bearer " $key))
          ($milliseconds (* $timeout 1000))
          ($body (iter-llm:request $messages $tools $model $max_tokens))
          ;; http:request is data: bind the complete request before submitting it.
          ($headers ((http:header "Content-Type" "application/json")
                     (http:header "Authorization" $authorization)
                     (http:header "X-APC-Tenant" "iter")
                     (http:header "x-session-id" $session)))
          ($request (http:request "POST" $url $headers $body $milliseconds 67108864)))
     (case (io:submit $request)
       (((io:pending $id) (iter-llm:outcome $id $timeout))
        ((Error $what $why) (Error "Request" $why))))))
