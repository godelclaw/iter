;; Gregorian calendar arithmetic and local wall-time conversion. Timezone
;; observations come from system:format-time; parsing and arithmetic are MeTTa.
!(import! &self (library str))
!(import! &self (library list))
!(import! &self (library system))
!(import! &self "lib_text.metta")

(= (calendar:failure) (Error "calendar" "expected a valid local YYYY-MM-DD HH:MM:SS date"))
(= (calendar:digits $text $min $max)
   (and (>= (str:char-length $text) $min)
     (and (<= (str:char-length $text) $max)
       (foldl-atom (str:python-codepoints $text) true $ok $code
         (and $ok (== (str:unicode-category $code) "Nd"))))))
;; Unicode decimal digits occur in consecutive groups of ten (some groups are
;; adjacent). Counting back to the start of an Nd run gives the decimal value.
(= (calendar:digit-value $code $count)
   (if (== (str:unicode-category (- $code 1)) "Nd")
       (calendar:digit-value (- $code 1) (+ $count 1)) (% $count 10)))
(= (calendar:number $text)
   (if (str:is-all-in digit $text) (str:string->number $text)
     (foldl-atom (str:python-codepoints $text) 0 $number $code
       (+ (* $number 10) (calendar:digit-value $code 0)))))

;; Match the accepted fields of strptime's numeric date format. Some leading
;; digits are ASCII ranges while a trailing digit may be Unicode Nd.
(= (calendar:field $kind $text)
   (and (calendar:digits $text 1 2)
     (if (== $kind month) (str:is-all-in digit $text)
       (if (== (str:char-length $text) 1) (if (== $kind day) (str:is-all-in digit $text) true)
         (let* (($first (str:char-slice $text 0 1)) ($last (str:char-slice $text 1 2)))
           (and (str:is-all-in digit $first)
             (let $n (str:string->number $first)
               (case $kind
                 ((day (or (is-member $n (1 2))
                           (and (is-member $n (0 3)) (str:is-all-in digit $last))))
                  (hour (or (<= $n 1) (and (== $n 2) (str:is-all-in digit $last))))
                  ($other (<= $n 5)))))))))))
(= (calendar:leap $year)
   (and (== (% $year 4) 0) (or (not (== (% $year 100) 0)) (== (% $year 400) 0))))
(= (calendar:month-days $year $month)
   (if (== $month 2) (if (calendar:leap $year) 29 28)
       (if (is-member $month (4 6 9 11)) 30 31)))

;; The input format's space accepts whitespace between the day and the clock.
;; Keep the original string for the tool's answer, rather than rewriting it.
(= (calendar:words $text)
   (str:split-whitespace
     (foldl-atom (text:scalars (9 10 11 12 13 28 29 30 31 32 133 160 5760
                              8192 8193 8194 8195 8196 8197 8198 8199 8200 8201 8202
                              8232 8233 8239 8287 12288))
       $text $out $space (str:join " " (str:split $space $out)))))

(= (calendar:parse $text $minimum-year-width)
   (case (str:split "-" $text)
     ((($year $month $rest)
       (if (and (calendar:digits $year $minimum-year-width 4)
                (and (calendar:field month $month) (== (text:rstrip $text) $text)))
           (case (calendar:words $rest)
             ((($day $clock)
               (case (str:split ":" $clock)
                 ((($hour $minute $second)
                   (if (and (calendar:day-prefix $rest $day)
                         (and (calendar:field day $day)
                         (and (calendar:field hour $hour)
                           (and (calendar:field minute $minute) (calendar:field second $second)))))
                       (calendar:checked (calendar:number $year) (calendar:number $month)
                         (calendar:number $day) (calendar:number $hour)
                         (calendar:number $minute) (calendar:number $second))
                       (calendar:failure)))
                  ($other (calendar:failure)))))
              ($other (calendar:failure))))
           (calendar:failure)))
      ($other (calendar:failure)))))

(= (calendar:day-prefix $rest $day)
   (let $leading (- (str:char-length $rest) (str:char-length (text:lstrip $rest)))
     (or (== $leading 0)
         (and (== $leading 1) (and (str:starts-with $rest " ")
           (and (== (str:char-length $day) 1) (str:is-all-in digit $day)))))))

(= (calendar:checked $year $month $day $hour $minute $second)
   (if (and (>= $year 1) (and (<= $year 9999)
       (and (>= $month 1) (and (<= $month 12)
       (and (>= $day 1) (and (<= $day (calendar:month-days $year $month))
       (and (>= $hour 0) (and (< $hour 24)
       (and (>= $minute 0) (and (< $minute 60)
       (and (>= $second 0) (< $second 60))))))))))))
       (CalendarParts $year $month $day $hour $minute $second)
       (calendar:failure)))

(= (calendar:before-year $year)
   (let $previous (- $year 1)
     (+ (- (+ (* 365 $previous) (floor-math (/ $previous 4)))
           (floor-math (/ $previous 100))) (floor-math (/ $previous 400)))))
(= (calendar:ordinal $year $month $day)
   (+ (+ (calendar:before-year $year)
         (index-atom (0 31 59 90 120 151 181 212 243 273 304 334) (- $month 1)))
      (+ $day (if (and (> $month 2) (calendar:leap $year)) 1 0))))
(= (calendar:naive (CalendarParts $year $month $day $hour $minute $second))
   (+ (* (- (calendar:ordinal $year $month $day) 719163) 86400)
      (+ (* $hour 3600) (+ (* $minute 60) $second))))

(= (calendar:local $epoch)
   (case (system:format-time "%Y-%m-%d %H:%M:%S" $epoch local)
     (((Error $what $why) (Error $what $why))
      ($text
       (case (calendar:parse $text 1)
         (((Error $what $why) (Error $what $why))
          ($parts (calendar:naive $parts))))))))

;; Solve wall = local(epoch), using the two surrounding UTC offsets. The
;; preceding-day observation selects the earlier fold (Python fold=0). In a
;; spring-forward gap the later epoch has the original pre-gap offset.
(= (calendar:local-epoch $wall)
   (let* (($a (- (calendar:local $wall) $wall))
          ($first (- $wall $a))
          ($first-wall (calendar:local $first))
          ($probe (- $first 86400))
          ($b (if (== $first-wall $wall) (- (calendar:local $probe) $probe)
                  (- $first-wall $first))))
     (if (and (== $first-wall $wall) (== $a $b)) $first
       (let* (($second (- $wall $b)) ($second-wall (calendar:local $second)))
         (if (== $second-wall $wall) $second
           (if (== $first-wall $wall) $first (max $first $second)))))))

(= (calendar:timestamp $text)
   (case (calendar:parse $text 4)
     (((Error $what $why) (Error $what $why))
      ($parts (catch (calendar:local-epoch (calendar:naive $parts)))))))
