(= (description) "Appends a stamp message.")
(= (transform $messages $tools)
   ((append $messages (cons-atom (json-object ((json-pair role "user") (json-pair content "[stamp]"))) ())) $tools))
