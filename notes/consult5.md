Prepared
========

  * SQLite WAL mode
  * Jack's reply
      - event merging
      - deliberate sleeping to facilitate merging and reduce context-switches
          * Beware of queue overflows!
  * Finalized Kočkopes reconciliation
      - use hashes as keys instead of original IDs
      - use XOR of values and a different hash instead of a Merkle tree
        (same as IBF does)

Ask
===

# Style / Form

  * Citing and links
      - The syntactic (or nonsyntactic) role of citations in sentence structure.
        Square-brackets vs. what the template does.
      - Citation as a confirmation for a statement (like square brackets on wikipedia)
        X "go see this!"
      - Linking to places in  the kernel source?
      - Linking to software homepages?
      - Linking to manual pages?

      * Links behave like parentheses, sentence should be meaningful w/o them,
        screw the template and get yourself links in square brackets.
      * In case of "go look here" links, you should somehow name the target
        and include the citation only as an addition. Like link text vs URL
        in HTML or the square bracket link convention in emails/markdown.
  * Saner page numbering that corresponds to PDF pages: is it allowable?
      * Not strictly forbidden but goes against some good conventions.
  * Usage of \noindent. E.g. in "the term inode is overloaded..." Is that correct?
    Essentially everywhere when not preceded by another paragraph?
      * Nope, distinguish logical paragraph breaks and in-paragraph/out-of-para
        items. If the list belonged to the previous paragraph and concludes it,
        the new paragraph should be indented.
  * ext2/3/4?
      * Talk about ext4, but clarify at the start that most applies to ext2/3
        unless specified otherwise.
  * filesystem X file system
      * probably "filesystem"
  * filesystem X filesystem type
      * should be clear from context

