;; A channel whose connection or state must outlive a single call keeps a
;; daemon: receive and send only exchange files with it. The daemon is this
;; module run after utils.metta with --daemon, started detached so that it
;; outlives the worker that started it. It holds a lock while it runs, which
;; is how a worker knows it is there, and a second daemon, started in a race,
;; finds the lock held and leaves. Its process id is written for whoever
;; stops it; nothing relies on it for liveness.

(= (example:inbox) "channels/.runtime/example/inbox")
(= (example:outbox) "channels/.runtime/example/outbox")
(= (example:lock-file) "channels/.runtime/example/daemon.lock")
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

(= (example:lock) (fs:open-write (example:lock-file) ("create") 384))

(= (example:running)
   (let* (($owner (example:lock))
          ($state (fs:lock $owner exclusive-nowait))
          ($closed (fs:close $owner)))
     (== $state fs:held)))

;; A start answers once the daemon holds its lock and has written its process
;; id, or after two seconds.
(= (example:started $tries)
   (if (or (<= $tries 0) (and (example:running) (fs:is-file (example:pid-file))))
       true
       (progn (sleep 0.02) (example:started (- $tries 1)))))
(= (example:start)
   (progn (fs:remove (example:pid-file))
          (proc:spawn ((cetta) "--lang" "petta" "--no-results" "utils.metta" "channels/_example.metta" "--daemon")
                      ((lifetime detached) (stdout null) (stderr null)))
          (example:started 100)))

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
          (let $owner (example:lock)
               (if (== (fs:lock $owner exclusive-nowait) fs:locked)
                   (progn (fs:write (example:pid-file) (text (system:pid)))
                          (example:serve))
                   (system:exit 0)))))

!(if (is-member --daemon (system:args)) (example:daemon) ())
