;; Iter's OpenAI-compatible model call in MeTTa, over CeTTa's native HTTP.
;; Replaces utils.py's init_client, chat_completion and exception_text; the
;; JSON helpers are utils.metta's. The request is the one utils.py sends.
!(import! &self (library io))
!(import! &self (library str))
!(use-module! uuid)

;; One session id per client, sent with the tenant on every request as
;; utils.py's default_headers did.
(= (init_client $base_url $api_key $timeout)
   (let $session (progn (translatePredicate (uuid $id ((Predicate (version 4))))) (repr $id))
        (progn (change-state! &iter_llm_client (client $base_url $api_key $timeout $session)) true)))

(= (exception_text $error) (repr $error))

;; Like model_dump(exclude_none=True): absent and null fields are the same.
(= (iter-llm:without-nulls $object)
   (json-object (filter-atom (json-pairs $object) $pair (not (== (json-pair-value $pair) null)))))

(= (iter-llm:request $messages $tools $model $max_tokens)
   (json-text (json-object ((json-pair model $model)
                            (json-pair messages (json-parse $messages))
                            (json-pair tools (json-parse $tools))
                            (json-pair tool_choice "required")
                            (json-pair max_tokens $max_tokens)
                            (json-pair enable_thinking true)))))

;; returns message_json finish_reason response usage, as utils.py did
(= (iter-llm:answer $text)
   (let* (($response (json-parse $text))
          ($choice (car-atom (json-get $response choices ())))
          ($message (iter-llm:without-nulls (json-get $choice message (json-object ()))))
          ($finish (string-to-atom (json-get $choice finish_reason "stop")))
          ($usage (json-get $response usage null)))
     ((json-text $message) $finish $text (if (== $usage null) "None" (json-text $usage)))))

(= (chat_completion $messages $tools $model $max_tokens)
   (let* (($client (get-state &iter_llm_client))
          ((client $base $key $timeout $session) $client)
          ($url (str:concat $base "/chat/completions"))
          ($authorization (str:concat "Bearer " $key))
          ($milliseconds (* $timeout 1000))
          ($body (iter-llm:request $messages $tools $model $max_tokens))
          ;; http:request is data: bind the complete request before submitting it.
          ;; Headers are built with cons-atom: an inline list headed by an
          ;; expression would be read as an application in a compiled body.
          ($headers (cons-atom (http:header "Content-Type" "application/json")
                      (cons-atom (http:header "Authorization" $authorization)
                        (cons-atom (http:header "X-APC-Tenant" "iter")
                          (cons-atom (http:header "x-session-id" $session) ())))))
          ($request (http:request "POST" $url $headers $body $milliseconds 67108864)))
     ;; io:try-submit takes the request as data (Atom); io:submit would evaluate it.
     (case (io:try-submit $request)
       (((io:pending $id)
         (case (io:try-wait $id $milliseconds)
           (((io:event $id (http:response 200 $text)) (iter-llm:answer $text))
            ((io:event $id (http:response $status $text))
             (translatePredicate (throw (llm_http_error $status $text))))
            ((io:idle) (progn (io:cancel $id) (translatePredicate (throw (llm_timeout $milliseconds)))))
            ($other (translatePredicate (throw (llm_transport_error $other)))))))
        ((io:failed) (translatePredicate (throw (llm_transport_error submit-refused))))))))
