;; Sends through a channel module, as the Python tool did: the channel is
;; loaded here and its (send $content) called. An (Error what why) from send
;; fails the tool, as an exception did. A Python channel's send(content) runs
;; in this worker's Python.
(= (description) "Send a message through a communication channel.")
(= (parameters) (channel content))

(= (send:python $path $content)
   (let* (($helper (import! &self iter_python.py))
          ($failure (py-call (iter_python.send (fs:canonical-path $path) $content))))
     (if (== $failure "") "SUCCESS" (Error $path $failure))))

(= (run $channel $content)
   (let $path (str-cat ("channels/" $channel ".metta"))
        (if (not (fs:is-file $path))
            (let $python (str-cat ("channels/" $channel ".py"))
                 (if (fs:is-file $python)
                     (send:python $python $content)
                     (str-cat ("Unknown channel: " $channel))))
            (let $module (fs:canonical-path $path)
                 (case (collapse (import! &self $module))
                   (((true) (let* (($call (cons-atom send (cons-atom $content ())))
                                   ($sent (eval $call)))
                              (case $sent
                                (((Error $what $why) $sent)
                                 ($done (if (== $sent $call) (str-cat ("Channel " $channel " cannot send")) "SUCCESS"))))))
                    ($none (Error $path "the channel module could not be loaded"))))))))
