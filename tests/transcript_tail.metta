;; The transcript transformation's helper on small fixtures, and descriptor
;; cleanup over repeated calls. Run from the repository root:
;;   cetta --lang petta tests/transcript_tail.metta < /dev/null
;; tests/transcript_tail_local.py compares the transformation with the Python
;; reference through the fresh-worker protocol.
!(import! &self "../utils.metta")
!(import! &self "../transformations/append_last_transcript_lines.metta")

(= (fixture $name) (str:concat "tests/fixtures/transcript/" $name))
(= (tools) (json:parse "[{\"name\": \"t\"}, {\"name\": \"t\"}]"))
(= (messages $content) (json:parse (str-cat ("[{\"role\": \"system\", \"content\": " $content ", \"x\": 1}, {\"role\": \"user\", \"content\": \"u\"}]"))))
(= (transformed $messages $path)
   (let ($new $tools) (transcript-tail:transform-path $messages (tools) $path)
     (json:stringify $new)))
(= (unchanged $messages $path)
   (let ($new $tools) (transcript-tail:transform-path $messages (tools) $path)
     (and (== $new $messages) (== $tools (tools)))))

;; the last six lines, with CR, CRLF and LF read as LF and the end stripped
!(test (transformed (messages "\" hi \\n\"") (fixture "lines.txt"))
       "[{\"role\":\"system\",\"content\":\" hi\\n\\ntranscript:\\nthree\\nfour\\nfive\\n\\nseven\",\"x\":1},{\"role\":\"user\",\"content\":\"u\"}]")
;; a missing key is added, parts get a part, other content stays
!(test (transformed (json:parse "[{\"role\": \"system\"}]") (fixture "unterminated.txt"))
       "[{\"role\":\"system\",\"content\":\"\\n\\ntranscript:\\nonly line, unterminated\"}]")
!(test (transformed (json:parse "[{\"content\": [{\"type\": \"text\", \"text\": \"a \"}]}]") (fixture "empty.txt"))
       "[{\"content\":[{\"type\":\"text\",\"text\":\"a \"},{\"type\":\"text\",\"text\":\"\\n\\ntranscript:\\n\"}]}]")
!(test (unchanged (messages "null") (fixture "lines.txt")) true)
!(test (unchanged (messages "7") (fixture "lines.txt")) true)
!(test (unchanged (json:parse "[]") (fixture "lines.txt")) true)
!(test (unchanged (json:parse "[\"s\"]") (fixture "lines.txt")) true)
;; a file that is missing, a directory, or not UTF-8 changes nothing
!(test (unchanged (messages "\"s\"") (fixture "missing.txt")) true)
!(test (unchanged (messages "\"s\"") "tests/fixtures/transcript") true)
!(test (unchanged (messages "\"s\"") (fixture "malformed.bin")) true)
;; the read size changes no answer
!(test (let* (($m (messages "\"s\"")) ($p (fixture "lines.txt"))
              ($a (transcript-tail:transform-path-chunk $m (tools) $p 1))
              ($b (transcript-tail:transform-path-chunk $m (tools) $p 5))
              ($c (transcript-tail:transform-path $m (tools) $p)))
         (and (== $a $b) (== $b $c)))
       true)
;; the helper's path is the argument; the transformation's default is a file in the workspace
!(test (transcript-tail:path) (system:getenv-or-default "ITER_TRANSCRIPT_PATH" "transcript.txt"))

;; every read closes its descriptor, whether it ends, fails to decode or fails to open
(= (descriptors) (size-atom (fs:entries "/proc/self/fd")))
(= (repeat $count $path)
   (if (<= $count 0) done
       (let $result (transcript-tail:transform-path (messages "\"s\"") (tools) $path)
         (repeat (- $count 1) $path))))
(= (leak $path)
   (let* (($before (descriptors)) ($done (repeat 300 $path))) (- (descriptors) $before)))
!(test (leak (fixture "lines.txt")) 0)
!(test (leak (fixture "malformed.bin")) 0)
!(test (leak (fixture "missing.txt")) 0)
!(test (leak "tests/fixtures/transcript") 0)
