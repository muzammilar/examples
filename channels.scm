;; Pinned Guix channel (the Guix analogue of flake.lock).
;; Update: guix time-machine -- describe -f channels > channels.scm
(list (channel
       (name 'guix)
       (url "https://codeberg.org/guix/guix.git")
       (branch "master")
       (commit "1eb2c7e1c3c0ee505d0696e31b2d81466f22dca2") ; 2026-09-28
       (introduction
        (make-channel-introduction
         "9edb3f66fd807b096b48283debdcddccfea34bad"
         (openpgp-fingerprint
          "BBB0 2DDF 2CEA F6A8 0D1D  E643 A2A0 6DF2 A33A 54FA")))))
