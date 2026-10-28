;; lib_text.metta, the Python text rules shared by transformations, on small
;; fixtures and against the transcript transformation's rstrip and universal
;; newline code, which it generalizes. Run from the repository root:
;;   cetta --lang petta tests/text_helper.metta < /dev/null
;; The output must hold no failed check (a line ending in the cross mark).
;; tests/alarms_local.py checks the whole alarm transformation against the
;; Python reference.
!(import! &self "../utils.metta")
!(import! &self "../lib_text.metta")
!(import! &self "../transformations/append_last_transcript_lines.metta")

(= (cp $code) (str:from-codepoints ($code)))
(= (cat $parts) (str:join "" $parts))

;; the code points of str.isspace, each alone and around text
(= (spaces) (9 10 11 12 13 28 29 30 31 32 133 160 5760 8192 8193 8194 8195 8196 8197 8198
             8199 8200 8201 8202 8232 8233 8239 8287 12288))
;; neighbours of those code points that are not whitespace
(= (others) (0 8 14 27 33 127 134 159 161 5759 5761 6158 8191 8203 8230 8231 8234 8238 8240
             8286 8288 12287 12289 65279 65533))
(= (all-stripped $codes)
   (if (== $codes ()) true
       (let ($code $rest) (decons-atom $codes)
         (and (and (== (text:strip (cat ((cp $code) "x" (cp $code)))) "x")
                   (and (== (text:lstrip (cat ((cp $code) "x" (cp $code)))) (cat ("x" (cp $code))))
                        (== (text:rstrip (cat ((cp $code) "x" (cp $code)))) (cat ((cp $code) "x")))))
              (all-stripped $rest)))))
(= (none-stripped $codes)
   (if (== $codes ()) true
       (let ($code $rest) (decons-atom $codes)
         (and (and (== (text:strip (cat ((cp $code) "x" (cp $code)))) (cat ((cp $code) "x" (cp $code))))
                   (== (text:rstrip (cat ("x" (cp $code)))) (cat ("x" (cp $code)))))
              (none-stripped $rest)))))
!(test (all-stripped (spaces)) true)
!(test (none-stripped (others)) true)
!(test (text:strip "") "")
!(test (text:strip "   ") "")
!(test (text:strip (cat ((cp 12288) (cp 8232) (cp 133) (cp 160)))) "")
!(test (text:strip " a b ") "a b")
!(test (text:strip "a") "a")
!(test (text:strip (cat ((cp 160) "a" (cp 8239) "\n\t "))) "a")
!(test (text:lstrip (cat ("\n " "a " "\n"))) (cat ("a " "\n")))
!(test (text:rstrip (cat ("\n " "a " "\n"))) (cat ("\n " "a")))
;; interior whitespace, NUL and multi-byte text stay
!(test (text:strip (cat (" " "a" (cp 0) " " (cp 233) (cp 8364) (cp 128512) " b" " "))) (cat ("a" (cp 0) " " (cp 233) (cp 8364) (cp 128512) " b")))
;; a wide scalar that merely ends in bytes of a space is not a space
!(test (text:strip (cat ((cp 8231) "x" (cp 8231)))) (cat ((cp 8231) "x" (cp 8231))))
!(test (text:strip (cat ((cp 65279) "x" (cp 65279)))) (cat ((cp 65279) "x" (cp 65279))))
;; the cuts of a long run are linear: this would not finish if each cut copied the text
(= (run $unit $count) (str:join "" (list:from-expression (repeat-unit $unit $count))))
(= (repeat-unit $unit $count) (if (<= $count 0) () (cons-atom $unit (repeat-unit $unit (- $count 1)))))
!(test (text:strip (cat ((run (cp 12288) 3000) "k" (run " " 3000)))) "k")
!(test (text:strip (run (cp 8232) 3000)) "")

;; universal newlines
!(test (text:universal-newlines "a\r\nb\rc\nd\r\n\r\re\n\r") "a\nb\nc\nd\n\n\ne\n\n")
!(test (text:universal-newlines "no newline") "no newline")
!(test (text:universal-newlines "") "")
!(test (text:universal-newlines "\r") "\n")
!(test (text:universal-newlines "\r\n") "\n")
!(test (text:universal-newlines (cat ("a" (cp 0) "\r" (cp 233) "\r\n" (cp 128512)))) (cat ("a" (cp 0) "\n" (cp 233) "\n" (cp 128512))))

;; the transcript transformation's code gives the same answers
(= (same-as-transcript $text)
   (and (== (text:rstrip $text) (transcript-tail:rstrip $text (transcript-tail:space)))
        (== (text:universal-newlines $text) (transcript-tail:universal $text))))
(= (corpus $codes)
   (if (== $codes ()) ()
       (let ($code $rest) (decons-atom $codes)
         (cons-atom (cat ("a" (cp $code) "b\r\n" (cp $code) "\r" (cp $code)))
           (cons-atom (cat ((cp $code) (cp $code) "\n"))
             (corpus $rest))))))
(= (all-same $texts)
   (if (== $texts ()) true
       (let ($text $rest) (decons-atom $texts)
         (and (same-as-transcript $text) (all-same $rest)))))
!(test (all-same (corpus (spaces))) true)
!(test (all-same (corpus (others))) true)
