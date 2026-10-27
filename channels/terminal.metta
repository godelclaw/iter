;; The terminal. receive answers the lines waiting on standard input, or "";
;; lines typed together arrive together. send writes a line to standard
;; output. The loop runs both on its own standard input and output.
(= (terminal:lines)
   (case (system:read-line 0)
     ((() ())
      (end_of_file ())
      ($line (if (is-string $line) (cons-atom $line (terminal:lines)) ())))))

(= (receive) (str:trim (str:join "\n" (terminal:lines))))

(= (send $content) (system:write stdout (str:concat (text $content) "\n")))
