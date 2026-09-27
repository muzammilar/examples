import csv
import MySQLdb



if __name__=="__main__":
    twitter_file_read=open('dengue_updated_country_timezone_url.csv','rb')              #twitter dump csv
    readingfile_twitter_dump=csv.reader(twitter_file_read)
    for data in readingfile_twitter_dump:
	try:
	    	db = MySQLdb.connect("localhost","root","NEWTLab123","ihealth" )
	    	#print "Connected."
	    	cursor = db.cursor()
	    	#print "Made Cursor."
	    	cursor.execute("INSERT INTO `dengue_timezone_loc_url`(`id`, `text`, `date`, `location`, `language`, `raw`, `file`, `timezone`, `gmt`, `country_from_country`, `country_from_location`, `url`) VALUES ('"+data[0]+"','"+data[1]+"','"+data[2]+"','"+data[3]+"','"+data[4]+"','"+data[5]+"','"+data[6]+"','"+data[7]+"','"+data[8]+"','"+data[9]+"','"+data[10]+"','"+data[11]+"')")
	    	#print " Cursor in execution"
	    	db.commit()
	    	#print " Cursor in execution committed"
	    	cursor.close()
	    	#print "Uploaded"
	except:
	    	print "Darn IT!"	
