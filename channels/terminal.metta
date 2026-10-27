;; The terminal. receive answers the lines waiting on standard input, or "";
;; lines typed together arrive together.
(= (terminal:lines)
   (let $ready (progn (translatePredicate (wait_for_input ((Predicate user_input)) $streams 0.0)) $streams)
        (if (== $ready ())
            ()
            (let $line (progn (translatePredicate (read_line_to_string user_input $read)) $read)
                 (if (== $line end_of_file) () (cons-atom $line (terminal:lines)))))))

(= (receive) (strip (join-text "\n" (terminal:lines))))

(= (send $content) (progn (translatePredicate (writeln $content)) true))
