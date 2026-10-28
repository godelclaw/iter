;; Append the last 6 transcript lines to the first message, as Iter's
;; transformations/append_last_transcript_lines.py does.
;;
;; The transcript is ITER_TRANSCRIPT_PATH, or transcript.txt in the Iter
;; workspace. transcript-tail:transform-path takes the path explicitly.
;;
;; The file is read in chunks by a strict streaming UTF-8 decoder that keeps
;; only the last six lines and the line being read, so a long old prefix is
;; scanned but not retained. Text is data throughout: it is never parsed or
;; evaluated. Any failure to read or decode leaves messages and tools as they
;; are. Line ends are Python's universal newlines: CR, LF and CRLF read as LF.
;;
;; Loaded by iter_worker.metta, whose utils.metta provides the libraries.
!(import! &self (library json))
!(import! &self (library str))
!(import! &self (library fs))
!(import! &self (library list))
!(import! &self (library system))

(= (description) "Append last 6 transcript lines to system message.")

(= (transcript-tail:lines) 6)
(= (transcript-tail:chunk-bytes) 65536)

(= (transcript-tail:path)
   (system:getenv-or-default "ITER_TRANSCRIPT_PATH" "transcript.txt"))

(= (transform $messages $tools)
   (transcript-tail:transform-path $messages $tools (transcript-tail:path)))

;; ($messages $tools) with the block appended to the first message, or both
;; unchanged when there is nothing to do or the transcript cannot be read.
;; $chunk is the size of each read, an allocation choice that bounds neither
;; the lines nor the file; a size below one reads the default.
(= (transcript-tail:transform-path $messages $tools $path)
   (transcript-tail:transform-path-chunk $messages $tools $path (transcript-tail:chunk-bytes)))
(= (transcript-tail:transform-path-chunk $messages $tools $path $chunk)
   (case $messages
     (((JsonArray $items)
       (if (== $items ())
           ($messages $tools)
           (case (car-atom $items)
             (((JsonObject $pairs)
               (case (transcript-tail:block $path (if (> $chunk 0) $chunk (transcript-tail:chunk-bytes)))
                 (((TranscriptBlockV1 $block)
                   (let $first (transcript-tail:append-block $pairs $block)
                     ((JsonArray (cons-atom $first (cdr-atom $items))) $tools)))
                  ($unavailable ($messages $tools)))))
              ($other ($messages $tools))))))
      ($other ($messages $tools)))))

;; The first message, an object, with the block appended to its content.
;; Python's dict semantics: the key keeps its first position and its last
;; value; String and parts content change, any other content stays.
(= (transcript-tail:append-block $pairs $block)
   (case (json:get-last (JsonObject $pairs) "content")
     ((JsonMissingV1
       (JsonObject (transcript-tail:put-content $pairs
                     (str:concat "\n\n" $block) false)))
      ((JsonFoundV1 $index $content)
       (case (get-type $content)
         ((String (JsonObject (transcript-tail:put-content $pairs
                    (str:concat (transcript-tail:rstrip $content (transcript-tail:space))
                                (str:concat "\n\n" $block))
                    false)))
          ($other (case $content
                    (((JsonArray $parts)
                      (JsonObject (transcript-tail:put-content $pairs
                        (JsonArray (list:append $parts
                          ((JsonObject ((JsonPair "type" "text")
                                        (JsonPair "text" (str:concat "\n\n" $block)))))))
                        false)))
                     ($untouched (JsonObject $pairs)))))))))))

;; The pairs with "content" set at its first place and removed from later ones,
;; or appended when there is none.
(= (transcript-tail:put-content $pairs $value $placed)
   (if (== $pairs ())
       (if $placed () ((JsonPair "content" $value)))
       (let ($head $tail) (decons-atom $pairs)
         (case $head
           (((JsonPair $key $old)
             (if (== $key "content")
                 (if $placed
                     (transcript-tail:put-content $tail $value true)
                     (cons-atom (JsonPair "content" $value)
                                (transcript-tail:put-content $tail $value true)))
                 (cons-atom $head (transcript-tail:put-content $tail $value $placed))))
            ($other (cons-atom $head (transcript-tail:put-content $tail $value $placed))))))))

;; Python's str.isspace code points, for rstrip: Zs, Zl, Zp and the controls
;; of bidirectional class B, S or WS.
(= (transcript-tail:space)
   (str:set (map-atom (9 10 11 12 13 28 29 30 31 32 133 160 5760
                       8192 8193 8194 8195 8196 8197 8198 8199 8200 8201 8202
                       8232 8233 8239 8287 12288)
                      $code (str:from-codepoints ($code)))))

;; The text without trailing whitespace. The last scalar is at most three
;; bytes, so each step looks only at the windows that end at the cut.
(= (transcript-tail:rstrip $text $space)
   (transcript-tail:rstrip-to $text $space (str:byte-length $text)))
(= (transcript-tail:rstrip-to $text $space $end)
   (let $width (transcript-tail:trailing-width $text $space $end 1)
     (if (== $width 0)
         (if (== $end (str:byte-length $text)) $text (str:byte-slice $text 0 $end))
         (transcript-tail:rstrip-to $text $space (- $end $width)))))
(= (transcript-tail:trailing-width $text $space $end $width)
   (if (or (> $width 3) (> $width $end))
       0
       (if (str:is-set-member $space (str:byte-slice $text (- $end $width) $end))
           $width
           (transcript-tail:trailing-width $text $space $end (+ $width 1)))))

;; (TranscriptBlockV1 "transcript:\n...") for the file, or
;; (TranscriptUnavailableV1 detail) when it cannot be opened, read or decoded.
(= (transcript-tail:block $path $chunk)
   (case (fs:open-read $path)
     (((fs:failed $reason $operation $failed $code $progress $message)
       (TranscriptUnavailableV1 $message))
      ($owner
       (case (transcript-tail:scan $owner $chunk (str:utf8-new strict)
                                   (TranscriptTailV1 () () false))
         (((TranscriptTailV1 $recent $fragments $held)
           (TranscriptBlockV1 (str:concat "transcript:\n"
              (transcript-tail:rstrip
                (str:join "" (transcript-tail:with-last $recent $fragments))
                (transcript-tail:space)))))
          ($failure $failure)))))))

;; The recent complete lines, and the unterminated last line if there is one.
(= (transcript-tail:with-last $recent $fragments)
   (if (== $fragments ())
       $recent
       (transcript-tail:keep-last
         (list:append $recent ((str:join "" (list:reverse $fragments)))))))

(= (transcript-tail:keep-last $lines)
   (list:drop (- (size-atom $lines) (transcript-tail:lines)) $lines))

;; Reads until the owner reports its end. Every exit closes the owner. A
;; chunk, empty or short, is never taken for the end.
(= (transcript-tail:scan $owner $chunk $decoder $state)
   (case (fs:read-chunk $owner $chunk)
     (((fs:chunk $bytes $offset)
       (case (str:utf8-feed $decoder $bytes false)
         (((Utf8DecodedV1 $text $next)
           (let $advanced (transcript-tail:advance $state $text false)
             (transcript-tail:scan $owner $chunk $next $advanced)))
          ($refused (transcript-tail:fail $owner $refused)))))
      ((fs:end $size)
       (case (str:utf8-feed $decoder "" true)
         (((Utf8DecodedV1 $text $next)
           (let $advanced (transcript-tail:advance $state $text true)
             (let $closed (fs:close $owner)
               $advanced)))
          ($refused (transcript-tail:fail $owner $refused)))))
      ($failure (transcript-tail:fail $owner $failure)))))

(= (transcript-tail:fail $owner $failure)
   (let $closed (fs:close $owner)
     (TranscriptUnavailableV1 $failure)))

;; Takes decoded text into the state: the last six complete lines, each with
;; its LF, the reversed pieces of the line in progress, and whether a CR ended
;; the text so that a following LF belongs to it. A CR before the end of the
;; file is a line end.
(= (transcript-tail:advance (TranscriptTailV1 $recent $fragments $held) $text $final)
   (let* (($joined (if $held (str:concat "\r" $text) $text))
          ($hold (and (not $final) (str:ends-with $joined "\r")))
          ($body (if $hold
                     (str:byte-slice $joined 0 (- (str:byte-length $joined) 1))
                     $joined)))
     (transcript-tail:absorb $recent $fragments $hold
                             (str:split "\n" (transcript-tail:universal $body)))))

;; CRLF and CR as LF.
(= (transcript-tail:universal $text)
   (case (str:find $text "\r")
     (((TextNotFound $missing) $text)
      ($found (str:join "\n" (str:split "\r"
                (str:join "\n" (str:split "\r\n" $text))))))))

;; $pieces are the parts of the text between LFs: all but the last are
;; complete lines, the first continuing the line in progress.
(= (transcript-tail:absorb $recent $fragments $held $pieces)
   (let $count (- (size-atom $pieces) 1)
     (if (== $count 0)
         (TranscriptTailV1 $recent (transcript-tail:push $fragments (car-atom $pieces)) $held)
         (let* (($kept (transcript-tail:keep-last (list:take $count $pieces)))
                ($whole (if (> $count (transcript-tail:lines))
                            $kept
                            (cons-atom (str:join "" (list:reverse
                                         (transcript-tail:push $fragments (car-atom $kept))))
                                       (cdr-atom $kept))))
                ($lines (map-atom $whole $piece (str:concat $piece "\n"))))
           (TranscriptTailV1 (transcript-tail:keep-last (list:append $recent $lines))
                             (transcript-tail:push () (index-atom $pieces $count))
                             $held)))))

(= (transcript-tail:push $fragments $piece)
   (if (== $piece "") $fragments (cons-atom $piece $fragments)))
