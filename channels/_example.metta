;; A channel whose connection or state must outlive a single call keeps a
;; daemon: receive and send only exchange files with it. The daemon is this
;; module run after utils.metta with --daemon, in a session of its own, so it
;; outlives the worker that started it.
!(import! &self (library system))

(= (example:inbox) "channels/.runtime/example/inbox")
(= (example:outbox) "channels/.runtime/example/outbox")
(= (example:pid-file) "channels/.runtime/example/pid")

(= (example:directories)
   (progn (translatePredicate (make_directory_path (example:inbox)))
          (translatePredicate (make_directory_path (example:outbox)))
          true))

;; the files of a directory in the order they were written
(= (example:entries $directory)
   (map-atom (sort-atom (filter-atom (directory_files $directory) $name (not (starts-with-underscore $name))))
             $name (atomics_to_string ($directory "/" $name))))
(= (example:message-files $directory)
   (filter-atom (example:entries $directory) $path (file-exists $path)))
(= (example:new-file $directory) (atomics_to_string ($directory "/" (system:monotonic-ns))))

(= (example:running)
   (if (file-exists (example:pid-file))
       (system:pid-alive? (progn (translatePredicate (number_string $pid (strip (read-text (example:pid-file))))) $pid))
       false))

(= (ensure_daemon)
   (progn (example:directories)
          (if (example:running)
              true
              (let $cetta (progn (translatePredicate (read_link "/proc/self/exe" $link $target)) $target)
                   (progn (translatePredicate (process_create $cetta ("--lang" "petta" "--no-results" "utils.metta" "channels/_example.metta" "--daemon")
                                                              ((Predicate (stdin null)) (Predicate (stdout null)) (Predicate (stderr null))
                                                               (Predicate (detached true)) (Predicate (process $pid)))))
                          (write-text (example:pid-file) (atomics_to_string ($pid))))))))

;; a file another worker removed first is skipped
(= (example:take $path)
   (case (collapse (catch (once (let $content (read-text $path) (progn (translatePredicate (delete_file $path)) $content)))))
         ((($content) (if (is-string $content) $content ""))
          ($none ""))))

(= (receive)
   (progn (ensure_daemon)
          (join-text "\n" (filter-atom (map-atom (example:message-files (example:inbox)) $path (example:take $path))
                                       $message (not (== $message ""))))))

(= (send $content)
   (progn (ensure_daemon)
          (write-text (example:new-file (example:outbox)) $content)))

(= (example:serve)
   (progn (map-atom (example:message-files (example:outbox)) $path
                    (let $content (example:take $path)
                         ;; Send $content to the external service here.
                         $content))
          ;; When an external message arrives:
          ;;   (write-text (example:new-file (example:inbox)) $message)
          (translatePredicate (sleep 0.1))
          (cut)
          (example:serve)))

(= (example:daemon)
   (progn (example:directories)
          (write-text (example:pid-file) (atomics_to_string ((system:pid))))
          (example:serve)))

!(if (is-member --daemon (system:args)) (example:daemon) ())
