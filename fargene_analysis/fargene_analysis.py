#!/usr/bin/env python2.7
import argparse
from os import path, makedirs, getcwd
from sys import argv
from collections import defaultdict
import logging
from multiprocessing import Pool, cpu_count
import itertools
import importlib
from Transformer import Transformer
from HmmModel import HmmModel
from predict_orfs import predict_orfs_orfFinder, predict_orfs_prodigal
from ResultsSummary import ResultsSummary
import utils
#import signal
#import time

def parse_args(argv):
    desc = 'Searches and retrieves new and previously known genes from fragmented metagenomic data and genomes'
    parser = argparse.ArgumentParser(description=desc)
    parser.add_argument('--infiles', '-i', nargs='+',required=True,
                        help='Input file(s) to be searched. Could either be in FASTA or FASTQ format.')
    parser.add_argument('--hmm-model',dest='hmm_model',required=True,
                        help='The Hidden Markov Model that should be used to analyse the data.'\
                        ' Could either be one of the pre-defined models or the path to a custom HMM.')
    parser.add_argument('--score','-sl',dest='long_score',required=False,
                        help = 'The threshold score for a sequence to be classified as a (almost) complete gene.')

    parser.add_argument('--meta',action='store_true',
                        help='If the input data is paired end metagenomic data.')
    parser.add_argument('--meta-score','-sm',dest='meta_score',type=float,
                        help = 'The threshold score for a fragment to be classified as a positive. '\
                        'Expressed as score per amino acid.')
    
    parser.add_argument('--output','-o',dest='out_dir',
                        help='The output directory for the whole run.')
    parser.add_argument('--force','-f',action='store_true',
                        help='If the output directory exists and you want to overwrite it.')
    
    parser.add_argument('--tmp-dir',dest='tmp_dir',
                        help='Directory for (sometimes large) intermediate files. '\
                                '(default: OUT_DIR/tmpdir)')

    parser.add_argument('--protein',action='store_true',dest='protein',
                        help= 'If the input sequence(s) is amino acids.')


    parser.add_argument('--processes','-p',type=int,default=1,dest='processes',
                        help = 'Number of processes to be used when processing metagenomic data.')

    parser.add_argument('--min-orf-length',type=int,dest='min_orf_length' ,
                        help='The minimal length for a retrieved predicted ORF (nt). '\
                                '(default: 90%% of the length of the chosen hmm.)')
   
    parser.add_argument('--retrieve-whole',action='store_true',dest='retrieve_whole',
                        help='Use this flag if the whole sequence where a hit is detected should be retrieved.')

    parser.add_argument('--no-orf-predict',action='store_false',dest='orf_predict' ,
                        help='No ORF prediction should be performed.')
    parser.add_argument('--no-quality-filtering',default=False,action='store_true',dest='no_quality_filtering',
                        help = 'Use if no quality control should be performed on the metagenomic data.')
    parser.add_argument('--no-assembly',action='store_true',dest='no_assembly',
                        help = 'Use if you want to skip the assembly and retrieval of contigs for metagenomic data.')
    parser.add_argument('--orf-finder',action='store_true',dest='orf_finder',
                        help = 'Use NCBI ORFfinder instead of prodigal for ORF prediction of genomes/contigs')


    parser.add_argument('--store-peptides','-sp',default=False,action='store_true',dest='store_peptides',
                        help = 'If the translated sequences should be stored. Useful if you plan to redo '\
                                 'the analysis using a different model and want to skip the preprocessing steps.')
    parser.add_argument('--rerun',action='store_true',
                        help = 'Use of you want to redo the analysis or do the analysis using a different model '\
                                'and have kept either the nucletide or amino acid sequences. '\
                                'Please note that this only works if the input data is the same for both runs')
    parser.add_argument('--amino-dir',dest='amino_dir',
                        help = 'Where the amino acid sequences generated by the method are located.'\
                                ' Only to be used in combination with --rerun')
    parser.add_argument('--fasta-dir',dest='fasta_dir',
                        help = 'Where the nucleotide sequences in FASTA generated by previous runs of the method are located. '\
                                'Only to be used in combination with --rerun')

    parser.add_argument('--translation-format',default='pearson',dest='trans_format',
            help= 'The translation format that transeq should use. (default: pearson)')

    parser.set_defaults(
            meta = False,
            retrieve_whole = False,
            protein = False,
            tmp_dir = False,
            hmm_model = None,
            long_score = None,
            meta_score = None,
            sensitive = False,
            no_assembly = False,
            transformer = True,
            orf_predict = True,
            min_orf_length = None,
            rerun = False,
            amino_dir = False,
            force = False,
            orf_finder = False,
            out_dir = './fargene_output')


    options = parser.parse_args()
    return options

def main():
    
    options = parse_args(argv)
    if path.isdir(options.out_dir) and not options.force:
        msg = ('The directory {0} already exists. To overwrite use the'
                ' --force flag').format(options.out_dir)
        print msg
        exit()
    else:
        utils.create_dir(options.out_dir)
    outdir = path.abspath(options.out_dir)

    try:
        logging.basicConfig(filename='%s/novelGeneFinder.log' %outdir,filemode='w',
            format='%(asctime)s %(levelname)s %(message)s', level=logging.DEBUG)
    except IOError as e:
        errorMsg ='Could not create logfile.\n\
                I/O error({0}): {1}'.format(e.errno, e.strerror)
        print errorMsg 
        
    logging.info('Starting pipeline\nAre to analyze %s files' %str(len(options.infiles)))
    logging.info('Running on %s processes' %str(options.processes))
    
    for infile in options.infiles:
        if not path.isfile(infile):
            msg ='The provided input file {0} does not exist.'.format(infile)
            print msg
            logging.critical(msg)
            logging.info('Exiting pipeline')
            exit()

    
    options.hmm_out_dir = '%s/hmmsearchresults' %(outdir)
    options.res_dir = '%s/retrievedFragments' %(outdir)
    if not options.tmp_dir:
        options.tmp_dir = '%s/tmpdir' %(outdir)
    options.final_gene_dir = '%s/predictedGenes' %(outdir)
    options.assembly_dir = '%s/spades_assembly' %(outdir)

    check_arguments(options)

    utils.create_dir(options.hmm_out_dir)
    utils.create_dir(options.tmp_dir)
    utils.create_dir(options.final_gene_dir)

    if options.meta:
        utils.create_dir(options.res_dir)
        if not options.no_quality_filtering:
            options.trimmed_dir = '%s/trimmedReads' %(path.abspath(options.res_dir))
            utils.create_dir(options.trimmed_dir)


    summaryFile = '%s/results_summary.txt' %(outdir)
    Results = ResultsSummary(summaryFile, len(options.infiles),options.hmm_model)

    print 'Starting fARGene'

    if not options.meta:
        parse_fasta_input(options,Results)
        Results.write_summary(False)
        numGenes = Results.retrievedSequences
        retrieved = 'possible genes'
    else:
        options.protein = False
        parse_fastq_input(options,Results)
        Results.write_summary(True)
        numGenes = Results.retrievedContigs
        retrieved = 'retrieved contigs'
    logging.info('Done with pipeline')
    
    msg = ('fARGene is done.\n'
            'Total number of {}: {}\n'
            'Total number of predicted ORFS longer than {} nt: {}\n'
            'Output can be found in {}'
            ).format(retrieved,numGenes,options.min_orf_length,Results.predictedOrfs,outdir)
    print msg

def check_arguments(options):
    predefined = False
    model_location = path.dirname(__file__)+ '/models'
    preDefinedModels = [
            HmmModel("b1", model_location + "/B1.hmm",135.8,float(0.2424)),
            HmmModel("class_b_1_2", model_location + "/class_B_1_2.hmm",127,float(0.3636)),
            HmmModel("class_b_3", model_location + "/class_B_3.hmm",103,float(0.30303)),
            HmmModel("class_a", model_location + "/class_A.hmm",105,float(0.2424)),
            HmmModel("class_c", model_location + "/class_C.hmm",248,float(0.30303)),
            HmmModel("class_d_1", model_location + "/class_D_1.hmm",182,float(0.3030)),
            HmmModel("class_d_2", model_location + "/class_D_2.hmm",234,float(0.3030)),
            HmmModel("qnr", model_location + "/qnr.hmm",150,float(0.51515))
            ]

    for model in preDefinedModels:
        if (options.hmm_model).lower()== model.name:
            options.hmm_model = model.path
            if not options.long_score:
                options.long_score = model.long_score
            if not options.meta_score:
                options.meta_score = model.meta_score
            predefined = True

    if not path.isfile(options.hmm_model):
        names = "\n".join([str(hmmModel.name) for hmmModel in preDefinedModels])
        msg = ("\nThe HMM file {0} could not be found.\n"
                 "Either provide a valid path to a HMM or choose "
                 "one of the following pre-defined models:\n{1}").format(
                         options.hmm_model,names)
        print msg
        logging.critical(msg)
        logging.info('Exiting pipeline')
        exit()

    if not predefined:
        if options.long_score is None:
            msg = "No threshold score for whole genes was given.\n"+\
            "Please provide one using the option --score"     
            print msg
            logging.critical(msg)
            exit()
        if options.meta and options.meta_score is None:
            msg = "No threshold score for metagenomic fragments was given.\n"+\
            "Please provide one using the option --meta-score"     
            print msg
            logging.critical(msg)
            exit()

    topFile = options.infiles[0]
    if options.meta:
        if not utils.is_fastq(topFile):
            msg = "If using the meta options, the input files must be FASTQ"
            logging.critical(msg)
            print msg
            exit()
    else:
        if not utils.is_fasta(topFile):
            msg = "If not using the meta option, the input file(s) must be FASTA"
            logging.critical(msg)
            print msg           
            exit()              

    if not options.min_orf_length:
        options.min_orf_length = utils.decide_min_ORF_length(options.hmm_model)

    if options.rerun:
        fastqBaseName = path.splitext(path.basename(options.infiles[0]))[0]
        if options.amino_dir:
            options.amino_dir = path.abspath(options.amino_dir)
        else:
            options.amino_dir = path.abspath(options.tmp_dir)
        peptideFile = '%s/%s-amino.fasta' %(options.amino_dir,fastqBaseName)
        if path.isfile(peptideFile):
            return
        else:
            if options.fasta_dir:
                options.fasta_dir = path.abspath(options.fasta_dir)
            else:
                options.fasta_dir = path.abspath(options.tmp_dir)
            fastaFile = '%s/%s.fasta' %(path.abspath(options.fasta_dir),fastqBaseName)
            if not path.isfile(fastaFile):
                msg = 'Neither nucleotide or amino sequences exists as FASTA.\n'\
                        'Please provide path to amino or nucleotide sequences or remove flag --rerun'
                logging.critical(msg)
                print msg + '\nExiting pipeline'
                logging.info('Exiting pipeline')
                exit()


def parse_fasta_input(options,Results):
    modelName = path.splitext(path.basename(options.hmm_model))[0]
    print 'Parsing FASTA files'
    frame = '6'
    for fastafile in options.infiles:
        fastaBaseName = path.basename(fastafile).rpartition('.')[0]
        hmmOut = '%s/%s-%s-hmmsearched.out' %(path.abspath(options.hmm_out_dir),fastaBaseName,modelName)
        fastaOut = '%s/%s-%s-filtered.fasta' %(path.abspath(options.final_gene_dir),fastaBaseName,modelName)
        aminoOut = '%s/%s-%s-filtered-peptides.fasta' %(path.abspath(options.final_gene_dir),fastaBaseName,modelName)
        orfFile = '%s/%s-%s-predicted-orfs.fasta' %(path.abspath(options.final_gene_dir),fastaBaseName,modelName)
        orfAminoFile = '%s/%s-%s-predicted-orfs-amino.fasta' %(path.abspath(options.final_gene_dir),fastaBaseName,modelName)
        hitFile = '%s/%s-positives.out' %(path.abspath(options.tmp_dir),fastaBaseName)
        elongated_fasta ='%s/%s-gene-elongated.fasta' %(path.abspath(options.tmp_dir),fastaBaseName)
        if options.protein:
            utils.perform_hmmsearch(fastafile,options.hmm_model,hmmOut,options)
            utils.classifier(hmmOut,hitFile,options)
            hitDict = utils.create_dictionary(hitFile,options)
            utils.retrieve_fasta(hitDict,fastafile,fastaOut,options)
        else: 
            if options.store_peptides:
                peptideFile ='%s/%s-amino.fasta' %(path.abspath(options.tmp_dir),fastaBaseName)
                utils.translate_sequence(fastafile,peptideFile,options,frame)
                logging.info('Performing hmmsearch')
                utils.perform_hmmsearch(peptideFile,options.hmm_model,hmmOut,options)
            else:
                utils.translate_and_search(fastafile,options.hmm_model,hmmOut,options)
#                utils.classifier(hmmOut,hitFile,options)
            utils.classifier(hmmOut,hitFile,options)
#            Results.count_hits(hitFile)
            hitDict = utils.create_dictionary(hitFile,options)
            utils.retrieve_fasta(hitDict,fastafile,fastaOut,options)
            if not path.isfile(fastaOut):
                exit()
            utils.retrieve_surroundings(hitDict,fastafile,elongated_fasta)
            if path.isfile(elongated_fasta):
                if not options.orf_finder:
                    tmpORFfile = '%s/%s-long-orfs.fasta' %(options.tmp_dir,fastaBaseName)
                    predict_orfs_prodigal(elongated_fasta,options.tmp_dir,tmpORFfile,options.min_orf_length) 
                    orfFile = utils.retrieve_predicted_orfs(options,tmpORFfile)
#                    predict_orfs_prodigal(elongated_fasta,options.tmp_dir,orfFile,options.min_orf_length) 
#                    utils.retrieve_predicted_genes_as_amino(options,orfFile,orfAminoFile,frame='1')
                    Results.count_orfs_genomes(orfFile)
                else:
                    tmpORFfile = '%s/%s-long-orfs.fasta' %(options.tmp_dir,fastaBaseName)
                    predict_orfs_orfFinder(elongated_fasta,options.tmp_dir, tmpORFfile,options.min_orf_length)
                    orfFile = utils.retrieve_predicted_orfs(options,tmpORFfile)
                    Results.predictedOrfs = Results.count_contigs(orfFile)
#                Results.count_orfs_genomes(orfFile)
            if options.store_peptides:
                options.retrieve_whole = False
                utils.retrieve_peptides(hitDict,peptideFile,aminoOut,options)
            else:
                tmpFastaOut = utils.make_fasta_unique(fastaOut,options)
                utils.retrieve_predicted_genes_as_amino(options,tmpFastaOut,aminoOut,frame='6')
        Results.count_hits(hitFile)
    return orfFile





def parse_fastq_input(options, Results):
    '''
    If the input is .fastq
    Pooled:
        1) Converts to .fasta with seqtk
        2) Translates to peptides and pipes to hmmsearch
        3) Parses the output ffrom hmmsearch and classifies the reads > hitFile
    4) Parses the hitFile and saves to dictionary,
        assumes paired end.
        If read_id_X from fileY_1 is a hit then it is saved
        as [fileY].append(read_id_X) and vice verse
        doing this after classification to save RAM
    5) Retrieves the hits in fastq using seqtk
    '''
    logging.info('Starting parse_fastq_input')
    modelName = path.splitext(path.basename(options.hmm_model))[0]
    fastqPath = path.dirname(path.abspath(options.infiles[0])) # Assuming the path is the same to every input fastqfile
    if options.processes > cpu_count():
        options.processes = cpu_count()

    if not options.rerun:
        print 'Converting FASTQ to FASTA'
        for fastqfile in options.infiles:
            fastqBaseName = path.splitext(path.basename(fastqfile))[0]
            fastafile = '%s/%s.fasta' %(path.abspath(options.tmp_dir),fastqBaseName)
            utils.convert_fastq_to_fasta(fastqfile,fastafile)
   
    p = Pool(options.processes)
    
    print 'Processing and searching input files. This may take a while...'

    try:
        bases_files = p.map(pooled_processing_fastq, itertools.izip((options.infiles),itertools.repeat(options)))  
    except KeyboardInterrupt:
        print "\nCaught a KeyboardInterrupt. Terminating..."
        p.terminate()
        p.join()
        exit()
        
    fastqDict = defaultdict(list)
    transformer = Transformer()
    transformer.find_file_difference(options.infiles[0],options.infiles[1])
    transformer.find_header_endings(options.infiles[0],options.infiles[1])

    print 'Retrieving hits from input files.'
    
    for fastqbase_hitfile in bases_files:
        fastqDict = utils.add_hits_to_fastq_dictionary(fastqbase_hitfile[1],
                fastqDict,fastqbase_hitfile[0],options,transformer)
    logging.info('Retrieving fastqfiles')
    utils.retrieve_paired_end_fastq(fastqDict,fastqPath,options,transformer) 
    
    if not options.no_quality_filtering:
        logging.info('Doing quality control')
        print 'Performing quality control'
        utils.quality(fastqDict.keys(),options)
    logging.info('Done')
    if not options.no_assembly:
        print 'Running assembly using SPAdes'
        logging.info('Running SPAdes')
        utils.run_spades(options)
        logging.info('Done')
        print 'Running retrieval of assembled genes.'
        logging.info('Running retrieval of assembled genes.')
        retrievedContigs,hits = utils.retrieve_assembled_genes(options)
        if path.isfile(retrievedContigs):
            print 'Predicting ORFS.'
            elongatedFasta ='%s/%s-gene-elongated.fasta' %(path.abspath(options.tmp_dir),path.basename(retrievedContigs).rpartition('.')[0])
            orfFile = '%s/%s-long-orfs.fasta' %(options.tmp_dir,path.basename(retrievedContigs).rpartition('.')[0])
            utils.retrieve_surroundings(hits,retrievedContigs,elongatedFasta)
            predict_orfs_orfFinder(elongatedFasta,options.tmp_dir,orfFile,options.min_orf_length) 
            retrievedOrfs = utils.retrieve_predicted_orfs(options,orfFile)
#            utils.retrieve_predicted_genes_as_amino(options,orfFile,orfAminoFile)
            Results.predictedOrfs = Results.count_contigs(retrievedOrfs)
        Results.retrievedContigs = Results.count_contigs(retrievedContigs)

def pooled_processing_fastq(fastqfile_options):
    try:
        fastqfile,options = fastqfile_options[0],fastqfile_options[1]
        modelName = path.splitext(path.basename(options.hmm_model))[0]
        fastqBaseName = path.splitext(path.basename(fastqfile))[0]
        fastqFilesBaseName = path.basename(fastqfile)
        fastafile = '%s/%s.fasta' %(path.abspath(options.tmp_dir),fastqBaseName)
        hmmOut = '%s/%s-%s-hmmsearched.out' %(path.abspath(options.hmm_out_dir),fastqBaseName,modelName)
        hitFile = '%s/%s-positives.out' %(path.abspath(options.tmp_dir),fastqBaseName)
        logging.info('Converting fastq to fasta')
        if options.rerun:
            peptideFile ='%s/%s-amino.fasta' %(options.amino_dir,fastqBaseName)
            if path.isfile(peptideFile):
                logging.info('Performing hmmsearch')
                utils.perform_hmmsearch(peptideFile,options.hmm_model,hmmOut,options)
            else:
                fastafile = '%s/%s.fasta' %(options.fasta_dir,fastqBaseName)
                logging.info('Translating and searching')
                utils.translate_and_search(fastafile,options.hmm_model,hmmOut,options)

        elif options.store_peptides:
            logging.info('Translating')
            peptideFile ='%s/%s-amino.fasta' %(path.abspath(options.tmp_dir),fastqBaseName)
            frame = '6'
            if not options.rerun:
                utils.translate_sequence(fastafile,peptideFile,options,frame)
            logging.info('Performing hmmsearch')
            utils.perform_hmmsearch(peptideFile,options.hmm_model,hmmOut,options)
        else:
            logging.info('Translating and searching')
            utils.translate_and_search(fastafile,options.hmm_model,hmmOut,options)
        logging.info('Start to classify')
        utils.classifier(hmmOut,hitFile,options)

        logging.info('Translating,searching and classification done')
        
        fastqPath = path.dirname(path.abspath(fastqfile)) # Assuming the path is the same to every input fastqfile
        return fastqFilesBaseName,hitFile
    except KeyboardInterrupt:
        raise KeyboardInterruptError()


class KeyboardInterruptError(Exception): pass


if __name__ == '__main__':
#    options = parse_args(argv)
    main()
