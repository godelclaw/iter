;; One persistent reader owns stdin. Workers take only complete published
;; lines; they never read or retain a fragment of the terminal stream.
(= (terminal:take $path)
   (let $claim (str-cat ($path ".claim-" (system:uuid4-hex)))
     (case (fs:rename $path $claim)
       ((() (let $line (json:parse (fs:read-text $claim))
              (if (is-string $line) (progn (fs:remove $claim) $line)
                (Error terminal "invalid input record; retained for inspection"))))
        ($lost "")))))
(= (receive)
   (let $directory (get-state &terminal-runtime)
     (if (== $directory "") ""
       (let $files (sort-atom (filter-atom (fs:entries $directory) $name
                          (and (str:ends-with $name ".json") (not (str:starts-with $name "_")))))
         (if (== $files ())
             (if (not (== (get-state &terminal-error) ""))
                 (Error terminal (get-state &terminal-error))
                 (if (fs:is-file (str:concat $directory "/error"))
                     (Error terminal (fs:read-text (str:concat $directory "/error"))) ""))
             (let $lines (map-atom $files $name (terminal:take (str-cat ($directory "/" $name))))
               (if (== (size-atom (filter-atom $lines $line (not (is-string $line)))) 0)
                   (str:trim (str:join "\n" $lines))
                   (Error terminal "invalid input record; retained for inspection"))))))))

(= (send $content) (system:write stdout (str:concat (text $content) "\n")))
