;; Sends through a channel module, as the Python tool did: the channel is
;; loaded here and its (send $content) called.
(= (description) "Send a message through a communication channel.")
(= (parameters) (channel content))

(= (run $channel $content)
   (let $path (atomics_to_string ("channels/" $channel ".metta"))
        (if (not (file-exists $path))
            (atomics_to_string ("Unknown channel: " $channel))
            (case (collapse (import! &self $path))
                  (((true) (let* (($call (cons-atom send (cons-atom $content ())))
                                    ($sent (eval $call)))
                               (if (== $sent $call)
                                   (atomics_to_string ("Channel " $channel " cannot send"))
                                   "SUCCESS")))
                   ($none (Error $path "the channel module could not be loaded")))))))
