;; Python's text rules for strings, shared by native transformations:
;; str.strip, str.lstrip and str.rstrip without arguments (the code points
;; of str.isspace) and the universal newlines of a text file (CR, LF and CRLF
;; read as LF). Text is data: it is never parsed or evaluated.
;;
;; Imported by a module as (import! &self lib_text), as utils is. It is not a
;; transformation, so it stays out of the transformations directory.
!(import! &self (library str))

;; Python's str.isspace code points: Zs, Zl, Zp and the controls of
;; bidirectional class B, S or WS.
(= (text:space)
   (str:set (text:scalars (9 10 11 12 13 28 29 30 31 32 133 160 5760
                           8192 8193 8194 8195 8196 8197 8198 8199 8200 8201 8202
                           8232 8233 8239 8287 12288))))
(= (text:scalars $codes)
   (if (== $codes ())
       ()
       (let ($code $rest) (decons-atom $codes)
         (cons-atom (str:from-codepoints ($code)) (text:scalars $rest)))))

;; The text without leading and trailing whitespace, as str.strip. A scalar is
;; at most three bytes, so each step looks only at the windows that start (or
;; end) at the cut and the work is linear in the whitespace removed.
(= (text:strip $text)
   (let* (($space (text:space))
          ($length (str:byte-length $text))
          ($start (text:leading-end $text $space 0 $length)))
     (if (== $start $length)
         ""
         (let $end (text:trailing-start $text $space $start $length)
           (if (and (== $start 0) (== $end $length))
               $text
               (str:byte-slice $text $start $end))))))

(= (text:lstrip $text)
   (let* (($length (str:byte-length $text))
          ($start (text:leading-end $text (text:space) 0 $length)))
     (if (== $start 0) $text (str:byte-slice $text $start $length))))

(= (text:rstrip $text)
   (let* (($length (str:byte-length $text))
          ($end (text:trailing-start $text (text:space) 0 $length)))
     (if (== $end $length) $text (str:byte-slice $text 0 $end))))

;; The offset of the first byte after the leading whitespace from $at.
(= (text:leading-end $text $space $at $length)
   (let $width (text:width-at $text $space $at $length 1)
     (if (== $width 0)
         $at
         (text:leading-end $text $space (+ $at $width) $length))))
(= (text:width-at $text $space $at $length $width)
   (if (or (> $width 3) (> (+ $at $width) $length))
       0
       (if (str:is-set-member $space (str:byte-slice $text $at (+ $at $width)))
           $width
           (text:width-at $text $space $at $length (+ $width 1)))))

;; The offset after the last non-whitespace scalar that starts at or after
;; $floor, looking back from $end.
(= (text:trailing-start $text $space $floor $end)
   (let $width (text:width-before $text $space $floor $end 1)
     (if (== $width 0)
         $end
         (text:trailing-start $text $space $floor (- $end $width)))))
(= (text:width-before $text $space $floor $end $width)
   (if (or (> $width 3) (> (+ $floor $width) $end))
       0
       (if (str:is-set-member $space (str:byte-slice $text (- $end $width) $end))
           $width
           (text:width-before $text $space $floor $end (+ $width 1)))))

;; CRLF and CR as LF, as Python's text mode reads a file.
(= (text:universal-newlines $text)
   (case (str:find $text "\r")
     (((TextNotFound $missing) $text)
      ($found (str:join "\n" (str:split "\r"
                (str:join "\n" (str:split "\r\n" $text))))))))
