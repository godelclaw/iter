;; Sends through a channel module, as the Python tool did: the channel is
;; loaded here and its (send $content) called. An (Error what why) from send
;; fails the tool, as an exception did.
(= (description) "Send a message through a communication channel.")
(= (parameters) (channel content))

(= (run $channel $content)
   (let $path (str-cat ("channels/" $channel ".metta"))
        (if (not (fs:is-file $path))
            (str-cat ("Unknown channel: " $channel))
            (let $module (fs:canonical-path $path)
                 (case (collapse (import! &self $module))
                   (((true) (let* (($call (cons-atom send (cons-atom $content ())))
                                   ($sent (eval $call)))
                              (case $sent
                                (((Error $what $why) $sent)
                                 ($done (if (== $sent $call) (str-cat ("Channel " $channel " cannot send")) "SUCCESS"))))))
                    ($none (Error $path "the channel module could not be loaded"))))))))
