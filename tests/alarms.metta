;; The alarm transformation's helper on small fixtures it writes itself, the
;; failure answers, and descriptor cleanup over repeated calls. Run from the
;; repository root:
;;   cetta --lang petta tests/alarms.metta < /dev/null
;; The output must hold no failed check (a line ending in the cross mark).
;; tests/alarms_local.py compares the transformation with the Python reference
;; through the fresh-worker protocol and measures it.
!(import! &self "../utils.metta")
!(import! &self "../transformations/alarms.metta")

;; fixtures live under .runtime, which git ignores; ALARMS_TEST_DIR moves them
(= (root) (system:getenv-or-default "ALARMS_TEST_DIR" ".runtime/alarms-test"))
(= (tools) (json:parse "[{\"name\": \"t\"}, {\"name\": \"t\"}]"))
(= (messages) (json:parse "[{\"role\": \"system\", \"content\": \"s\", \"x\": 1}, {\"role\": \"system\", \"content\": \"s\", \"x\": 1}]"))
(= (put $directory $name $text)
   (let* (($made (fs:make-directories $directory 511)) ($written (fs:write (str:concat (str:concat $directory "/") $name) $text))) $written))
(= (names $directory) (fs:entries $directory))
;; the transformed messages as JSON text, or the failure
(= (run $directory $now $stamp)
   (case (alarms:transform-at (messages) (tools) $directory $now $stamp)
     ((($new $kept) (json:stringify $new))
      ($failure $failure))))
(= (fresh $name)
   (let* (($directory (str:concat (str:concat (root) "/") $name))
          ($made (fs:make-directories $directory 511))
          ($entries (fs:entries $directory))
          ($cleared (clear $directory $entries)))
     $directory))
(= (clear $directory $entries)
   (if (== $entries ()) done
       (let ($name $rest) (decons-atom $entries)
         (let* (($removed (fs:remove (str:concat (str:concat $directory "/") $name))) ($more (clear $directory $rest))) $more))))
(= (prefix) "[{\"role\":\"system\",\"content\":\"s\",\"x\":1},{\"role\":\"system\",\"content\":\"s\",\"x\":1}")

(= (description-is-python) (description))
!(test (description-is-python) "Alarm clock handling")

;; no alarms: the messages and tools come back as given, and the directory exists
!(test (let $d (fresh "empty") (let $r (alarms:transform-at (messages) (tools) $d 1000 "S") (== $r ((messages) (tools))))) true)
!(test (let $d (str:concat (root) "/made/sub/dir") (let $r (alarms:transform-at (messages) (tools) $d 1000 "S") (fs:entries $d))) ())
;; due alarms in name order (100, 1000, 20: not numeric order), equal time due,
;; future and non-numeric entries kept
(= (sorted $names) (alarms:sort $names))
!(test (let* (($d (fresh "order"))
              ($a (put $d "100" "a\nfirst"))
              ($b (put $d "20" "terminal text"))
              ($c (put $d "1000" "keep"))
              ($e (put $d "1001" "future"))
              ($f (put $d "notes.txt" "not a time"))
              ($g (put $d "100x" "not a time either"))
              ($m (run $d 1000 "T")))
         ($m (sorted (fs:entries $d))))
       ((str:concat (prefix) ",{\"role\":\"user\",\"content\":\"Step T: [a] [ALARM] first\"},{\"role\":\"user\",\"content\":\"Step T: [terminal] [ALARM] keep\"},{\"role\":\"user\",\"content\":\"Step T: [terminal] [ALARM] terminal text\"}]")
        ("1001" "100x" "notes.txt")))
;; the sort is byte order (a prefix first), over any count
!(test (alarms:sort ("9" "10" "1e1" "+3" "-1" ".5" "05" "100" "1" "1.5" "0"))
       ("+3" "-1" ".5" "0" "05" "1" "1.5" "10" "100" "1e1" "9"))
!(test (alarms:sort ()) ())
!(test (alarms:sort ("only")) ("only"))
(= (numbered $count) (if (<= $count 0) () (cons-atom (str:number->string (% (* $count 7919) 100003)) (numbered (- $count 1)))))
(= (in-order $names)
   (if (== (cdr-atom $names) ()) true
       (let ($a $rest) (decons-atom $names)
         (and (<= (str:compare $a (car-atom $rest)) 0) (in-order $rest)))))
!(test (let $sorted (alarms:sort (numbered 3000)) (and (== (size-atom $sorted) 3000) (in-order $sorted))) true)
;; the second invocation finds nothing more
!(test (let* (($d (fresh "twice")) ($a (put $d "1" "x")) ($m (run $d 5 "T")) ($n (run $d 5 "T")))
         ($n (fs:entries $d)))
       ((str:concat (prefix) "]") ()))
;; text is inert: expression shapes, quotes, NUL, non-ASCII, mixed newlines, outer whitespace
!(test (let* (($d (fresh "inert"))
              ($a (put $d "1" (str:join "" ("  \r\n" "tg\r(= (transform $m $t) boom) !(shell \"x\") \\ 'q' $v &self" (str:from-codepoints (0 233 128512)) "\r\n\n " (str:from-codepoints (12288)))))))
         (run $d 1 "T"))
       (str:join "" ((prefix) ",{\"role\":\"user\",\"content\":\"Step T: [tg] [ALARM] (= (transform $m $t) boom) !(shell \\\"x\\\") \\\\ 'q' $v &self\\u0000é😀\"}]")))
;; a zero-byte file is an empty terminal message
!(test (let* (($d (fresh "zero")) ($a (put $d "7" ""))) (run $d 7 "T"))
       (str:concat (prefix) ",{\"role\":\"user\",\"content\":\"Step T: [terminal] [ALARM] \"}]"))

;; faults are explicit failures. The alarms handled before one stay removed; the
;; file that failed stays.
(= (failed $result) (case $result (((Error $what $why) true) ($other false))))
!(test (let* (($d (fresh "directory")) ($a (put $d "1" "x")) ($b (fs:make-directories (str:concat $d "/2") 511)) ($m (run $d 5 "T")))
         ((failed $m) (sorted (fs:entries $d))))
       (true ("2")))
!(test (let* (($d (fresh "malformed"))
              ($a (put $d "1" "x"))
              ($raw (fs:read-text-result "tests/fixtures/transcript/malformed.bin" 0 "raw"))
              ($b (case $raw ((("ok" $bytes) (put $d "2" $bytes)))))
              ($c (put $d "3" "z"))
              ($m (run $d 5 "T")))
         ((failed $m) (sorted (fs:entries $d))))
       (true ("2" "3")))
!(test (failed (alarms:transform-at (json:parse "{}") (tools) (fresh "notarray") 5 "T")) true)
!(test (failed (alarms:transform-at (messages) (tools) (fresh "notnumber") "5" "T")) true)
!(test (failed (alarms:transform-at (messages) (tools) (fresh "notstamp") 5 7)) true)
!(test (failed (alarms:transform-at (messages) (tools) "tests/alarms.metta" 5 "T")) true)

;; the public transformation reads ITER_ALARMS_DIR, by default memory/alarms
!(test (alarms:directory) (system:getenv-or-default "ITER_ALARMS_DIR" "memory/alarms"))

;; Deadline spelling follows float(name); injected integer clocks remain exact.
!(test (alarms:due "9007199254740993" 9007199254740992) true)
!(test (alarms:due "9007199254740995" 9007199254740995) false)
!(test (alarms:due "9007199254740996.0" 9007199254740995) false)
!(test (alarms:due "9223372036854775807" 9223372036854775807) false)
!(test (alarms:due "9007199254740993" 9007199254740992.0) true)
!(test (alarms:due "-9007199254740993" -9007199254740993) false)
!(test (alarms:due "1.5" 1) false)
!(test (alarms:due "1.5" 2) true)
!(test (alarms:valid-time (json:parse-python "NaN")) false)
!(test (alarms:valid-time (json:parse-python "Infinity")) false)
!(test (alarms:valid-time (json:parse-python "-Infinity")) false)

;; every call closes its directory owner, whether it succeeds or fails
(= (descriptors) (size-atom (fs:entries "/proc/self/fd")))
(= (repeat $count $directory)
   (if (<= $count 0) done
       (let $result (run $directory 5 "T") (repeat (- $count 1) $directory))))
(= (leak $directory)
   (let* (($before (descriptors)) ($done (repeat 300 $directory))) (- (descriptors) $before)))
!(test (leak (fresh "leak-empty")) 0)
!(test (let* (($d (fresh "leak-future")) ($a (put $d "99999" "x"))) (leak $d)) 0)
!(test (let* (($d (fresh "leak-fault")) ($a (put $d "1" "x")) ($b (fs:make-directories (str:concat $d "/2") 511))) (leak $d)) 0)
!(test (leak "tests/alarms.metta") 0)
;; a listing that fails part way still closes its owner
!(test (let* (($d (fresh "leak-closed")) ($o (fs:open-directory $d)) ($c (fs:close $o)) ($before (descriptors))
              ($r (alarms:drain $o 5 ())))
         (and (failed $r) (== (descriptors) $before)))
       true)
