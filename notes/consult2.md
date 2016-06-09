## Fanotify, patches

  * Jak se to chová při změnách způsobených `mmap`-em?
  * Problém: syscally nejsou zdaleka jediná cesta, jak způsobit rename (e.g. NFS server)
      - Ale stávající fanotify na tom není o moc líp.
      - Z principu to ani o moc líp nejde, fanotify je vázané na vfsmount, takže co se
        neděje prostřednictvím konkrétního vfsmountu, nemůže zaznamenat.
      - Přinejmenším všechny zásahy z userspace by to zachytit mělo, protože security
        moduly kontrolují právo udělat např. rename právě podle cesty (struct path),
        a právě na ta místa, kde se volá e.g. `security_path_rename`, můžu přidat své
        notifikace.
      - Výsledný patch je velmi jednoduchý.
      - Nakonec Medvěd uznal, že je to celkem pěkné.
  * Možná se zeptat Honzy Káry

## Vnitřní struktura, ukládání

  * Jak ukládat metadata? Sqlite?
      - Problém: sqlite buď při každém zápisu syncuje, nebo nezaručuje integritu.
        Různá dbm jsou na tom pravděpodobně podobně.
      - MJ: Co by mohlo fungovat, pokud nevadí, že se občas posledních pár změn
        ztratí: ukládám si nové změny jen do logu, a když se zaplní, syncnu ho a aplikuju
        je. Při recovery nejdřív aplikuji log, pak načítám.

## Synchronizační algoritmus
  - log2(U) moc velké
      * zvětšení arity
          - Post-konzult: Pomůžu si tím? Pro binární strom přenesu průměrně
            2*#změn*hloubka = 2*N*log2(U) (vždy rodiče a nezměněného sourozence).
            Pro třeba 128-ární strom to bude 128*N*log128(U) = 128/log2(128)*N*log2(U),
            protože pro každý vrchol na cestě do kořene přenesu i zbylých 127 nezmeněných
            synů, abych poznal, že se nezměnili.
      * nedalo by se na to dívat jako na komprimovanou trii?
  - síťová komunkace
      * nemůžu čekat pokaždé na roundtrip
      * ale neměl bych ani jen tak chrlit data, protože na síti s velkým
        bandwidth, ale velkou latencí, přenesu spoustu dat zbytečně
        (např. satelitní připojení)
      * kompromis: přenášet po rozumně velkých blocích a počkat si na
        odpověď

