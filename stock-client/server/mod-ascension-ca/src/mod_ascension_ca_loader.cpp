/*
 * mod-ascension-ca — script loader.
 *
 * AzerothCore's module CMake generates a call to Add<dir-with-underscores>Scripts()
 * for every module directory, so the name below is fixed by the directory name
 * "mod-ascension-ca".
 */

void AddSC_AscensionCA();

void Addmod_ascension_caScripts()
{
    AddSC_AscensionCA();
}
