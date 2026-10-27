;; A channel whose connection or state must outlive a single call keeps a
;; daemon: receive and send only exchange files with it. The daemon is this
;; module run after utils.metta with --daemon. A process job ends with its
;; owner, so a shell starts the daemon in the background, with its output
;; discarded, and the daemon outlives the worker that started it.

(= (example:inbox) "channels/.runtime/example/inbox")
(= (example:outbox) "channels/.runtime/example/outbox")
(= (example:pid-file) "channels/.runtime/example/pid")

(= (example:directories)
   (let* ((() (fs:make-directories (example:inbox) 511))
          (() (fs:make-directories (example:outbox) 511)))
     true))

;; the files of a directory in the order they were written
(= (example:message-files $directory)
   (filter-atom (map-atom (sort-atom (filter-atom (fs:entries $directory) $name (not (str:starts-with $name "_"))))
                          $name (str-cat ($directory "/" $name)))
                $path (fs:is-file $path)))
(= (example:new-file $directory) (str-cat ($directory "/" (system:monotonic-ns))))

(= (example:running)
   (if (fs:is-file (example:pid-file))
       (== (system:pid-alive? (str:string->number (str:trim (fs:read-text (example:pid-file))))) true)
       false))

;; the shell prints the daemon's process id
(= (example:start)
   (case (proc:run ("sh" "-c" "\"$0\" \"$@\" >/dev/null 2>&1 & echo $!"
                    (cetta) "--lang" "petta" "--no-results" "utils.metta" "channels/_example.metta" "--daemon")
                   ((stderr null)))
     (((proc:result (proc:exited 0) $cause $out $err $capture) (fs:write (example:pid-file) (str:trim $out))))))

(= (ensure_daemon)
   (progn (example:directories)
          (if (example:running) true (example:start))))

;; a file another worker removed first is skipped
(= (example:take $path)
   (let $content (fs:read-text $path)
        (if (is-string $content) (if (== (fs:remove $path) ()) $content "") "")))

(= (receive)
   (progn (ensure_daemon)
          (str:join "\n" (filter-atom (map-atom (example:message-files (example:inbox)) $path (example:take $path))
                                      $message (not (== $message ""))))))

(= (send $content)
   (progn (ensure_daemon)
          (fs:write (example:new-file (example:outbox)) $content)))

(= (example:serve)
   (progn (map-atom (example:message-files (example:outbox)) $path
                    (let $content (example:take $path)
                         ;; Send $content to the external service here.
                         $content))
          ;; When an external message arrives:
          ;;   (fs:write (example:new-file (example:inbox)) $message)
          (sleep 0.1)
          (cut)
          (example:serve)))

(= (example:daemon)
   (progn (example:directories)
          (fs:write (example:pid-file) (text (system:pid)))
          (example:serve)))

!(if (is-member --daemon (system:args)) (example:daemon) ())
