;; Calls the transcript helper with an explicit path and read size, and answers
;; the result as JSON text. The worker protocol calls any function by name.
;; Paths are relative to the Iter workspace, where modules run.
!(import! &self "transformations/append_last_transcript_lines.metta")
(= (helper $messages $tools $path $chunk)
   (let ($new $kept) (transcript-tail:transform-path-chunk $messages $tools $path $chunk)
     (json:stringify (JsonArray ($new $kept)))))
(= (helper-default $messages $tools $path)
   (let ($new $kept) (transcript-tail:transform-path $messages $tools $path)
     (json:stringify (JsonArray ($new $kept)))))
