!(import! &self "../lib_reliability.metta")
(= (description) "Revises tool reliability for each matching result in the current context, including replayed results.")
(= (transform $messages $tools) (rel:transform $messages $tools))
